import qrcode
import stripe
import json
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta
from flask_socketio import leave_room
from cryptography.fernet import Fernet
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from flask_socketio import join_room, emit
from booking.booking_service import acquire_lease, confirm_booking
from booking.redis_utils import redis_sadd, redis_srem, redis_smembers, redis_hget, redis_hset, redis_delete, redis_hdel, redis_delete_lease, redis_keys, redis_get, redis_safe_release_lease
from booking.forms import BookingForm
from config import City, db, ParkingLot, Booking, ParkingSpot, app, socketio, redis_client, secrets
from booking.utils import emit_to_relevant_rooms_about_booking

booking_bp = Blueprint('booking_bp', __name__, template_folder='templates')


@booking_bp.route('/booking', methods=['GET', 'POST'])
@login_required
def booking_form():
    form = BookingForm()
    cities = City.query.all()
    form.city.choices = [(city.id, city.city) for city in cities]
    return render_template('booking/booking.html', form=form)


@booking_bp.route('/city_selected', methods=['POST'])
def city_selected():
    data = request.get_json()
    city_id = data.get('city')
    parkingLots = ParkingLot.query.filter_by(city_id=city_id).all()

    return jsonify([{
        'id': lot.id,
        'name': lot.name,
        'lat': lot.lat,
        'long': lot.long, } for lot in parkingLots])


def create_booking_from_session(session, spot):
    """Create booking from Stripe session using new leasing data"""
    return Booking(
        userid=int(session.metadata.get('user_id')),
        parking_lot_id=int(session.metadata.get('parking_lot_id')),
        spot_id=int(session.metadata.get('spot_id')),
        bookingDate=datetime.strptime(session.metadata.get('booking_date'), '%Y-%m-%d').date(),
        startTime=datetime.strptime(session.metadata.get('start_time'), '%H:%M').time(),
        endTime=datetime.strptime(session.metadata.get('end_time'), '%H:%M').time(),
        amount=float(session.amount_total) / 100,
    )


@booking_bp.route('/payment_success', methods=['GET'])
def payment_success():
    session_id = request.args.get('session_id')
    current_app.logger.info(f"payment_success called with session_id: {session_id}")

    if not session_id:
        current_app.logger.error("No session_id provided in payment_success")
        flash("Invalid payment session. Please try again.", "error")
        return redirect(url_for('booking_bp.booking_form'))

    try:
        current_app.logger.info(f"Retrieving Stripe session: {session_id}")
        session = stripe.checkout.Session.retrieve(session_id)
        current_app.logger.info(f"Stripe session retrieved: {session.id}, status: {session.payment_status}")

        reservation_id = session.metadata.get('reservation_id')
        spot_id = session.metadata.get('spot_id')
        parking_lot_id = session.metadata.get('parking_lot_id')
        booking_date = session.metadata.get('booking_date')
        start_time = session.metadata.get('start_time')
        end_time = session.metadata.get('end_time')
        user_id = session.metadata.get('user_id')

        current_app.logger.info(f"Session metadata - reservation_id: {reservation_id}, spot_id: {spot_id}, "
                                f"parking_lot_id: {parking_lot_id}, booking_date: {booking_date}, "
                                f"start_time: {start_time}, end_time: {end_time}, user_id: {user_id}")

        if not all([reservation_id, spot_id, parking_lot_id, booking_date, start_time, end_time, user_id]):
            current_app.logger.error("Missing required metadata in Stripe session")
            flash("Invalid payment session data. Please try again.", "error")
            return redirect(url_for('booking_bp.booking_form'))

        current_app.logger.info(f"Verifying spot exists: {spot_id}")
        spot = ParkingSpot.query.get(spot_id)
        if not spot:
            current_app.logger.error(f"Spot not found: {spot_id}")
            flash("Invalid spot. Please try again.", "error")
            return redirect(url_for('booking_bp.booking_form'))

        booking_data = {
            'parking_lot_id': parking_lot_id,
            'booking_date': booking_date,
            'start_time': start_time,
            'end_time': end_time
        }

        idempotency_key = f"stripe_{session_id}"
        current_app.logger.info(f"Using idempotency key: {idempotency_key}")

        current_app.logger.info(f"Attempting to confirm booking for reservation: {reservation_id}")
        result, status_code = confirm_booking(
            reservation_id=reservation_id,
            spot_id=spot_id,
            user_id=user_id,
            booking_data=booking_data,
            idempotency_key=idempotency_key
        )

        current_app.logger.info(f"Booking confirmation result: {result}, status_code: {status_code}")

        if status_code != 200:
            current_app.logger.error(f"Booking failed with status {status_code}. Issuing refund.")
            try:
                refund = stripe.Refund.create(payment_intent=session.payment_intent)
                current_app.logger.info(f"Refund issued: {refund.id}")
                flash("Booking failed. Refund issued. Please try again.", "error")
            except stripe.error.StripeError as refund_error:
                current_app.logger.error(f"Refund failed: {str(refund_error)}")
                flash("Booking failed. Please contact support for refund.", "error")
            return redirect(url_for('booking_bp.booking_form'))

        booking_id = result.get('booking_id')
        current_app.logger.info(f"Booking successful! Booking ID: {booking_id}")

        if not booking_id:
            current_app.logger.warning("Booking completed but no booking_id returned")
            flash("Booking completed but could not retrieve booking details.", "warning")
            return redirect(url_for('dashboard.dashboard'))

        current_app.logger.info(f"Fetching booking from database: {booking_id}")
        new_booking = Booking.query.get(booking_id)
        if not new_booking:
            current_app.logger.warning(f"Booking not found in database: {booking_id}")
            flash("Booking completed but details not found.", "warning")
            return redirect(url_for('dashboard.dashboard'))

        current_app.logger.info("Generating QR code")
        generate_qr_code(new_booking.id)

        current_app.logger.info("Disconnecting user sockets")
        disconnect_user(session)

        current_app.logger.info("Payment and booking process completed successfully!")
        flash("Your booking and payment were successful!", "success")
        return redirect(url_for('dashboard.dashboard'))

    except stripe.error.StripeError as e:
        current_app.logger.error(f"Stripe error in payment_success: {str(e)}", exc_info=True)
        flash("Payment processing error. Please contact support.", "error")
        return redirect(url_for('booking_bp.booking_form'))
    except Exception as e:
        current_app.logger.error(f"Unexpected error in payment_success: {str(e)}", exc_info=True)
        flash("Payment received! If your booking doesn't appear, contact support.", "warning")
        return redirect(url_for('dashboard.dashboard'))


def generate_qr_code(new_booking_id):
    key = secrets["FERNET_KEY"]
    cipher = Fernet(key.encode())
    encrypted = cipher.encrypt(str(new_booking_id).encode()).decode()

    img = qrcode.make(encrypted)
    img.save(f"static/qr_codes/{new_booking_id}.png")


def disconnect_user(session):
    user_id = session.metadata['user_id']
    current_app.logger.info(f"disconnect_user called for user_id: {user_id}")

    room_keys = redis_keys("active_rooms:*")
    current_app.logger.info(f"Found {len(room_keys)} active rooms")

    for room_key in room_keys:
        room_name = room_key.replace("active_rooms:", "")
        sids = redis_smembers(room_key)
        current_app.logger.info(f"Room {room_name} has {len(sids)} connections")

        user_sids = {sid for sid in sids if sid.startswith(f"{user_id}_")}
        current_app.logger.info(f"User {user_id} has {len(user_sids)} connections in room {room_name}")

        for sid in user_sids:
            conn_data = redis_hget("active_connections", sid) or {}
            reservation_id = conn_data.get('reservation_id')

            if reservation_id:
                lease_data = redis_client.hgetall(f"lease_data:{reservation_id}")
                if lease_data and any(key in [b'stripe_session_id', 'stripe_session_id'] for key in lease_data.keys()):
                    current_app.logger.info(f"Preserving payment lease {reservation_id} for sid {sid}")
                    continue

            current_app.logger.info(f"Disconnecting sid {sid} from room {room_name}")
            emit('payment_complete', {}, room=sid)
            socketio.disconnect(sid)

            redis_srem(room_key, sid)


@socketio.on('connect')
def handle_connect():
    print("Client connected: ", request.sid)
    redis_hset("active_connections", request.sid, {
        'connected_at': datetime.now(ZoneInfo("Europe/Nicosia")).isoformat(),
        'rooms': '[]',
        'user_id': str(current_user.get_id()) if current_user.is_authenticated else 'anonymous'
    })


@socketio.on('connect')
def handle_connect():
    print("Client connected: ", request.sid)
    redis_hset(redis_client, "active_connections", request.sid, {
        'connected_at': datetime.now(ZoneInfo("Europe/Nicosia")).isoformat(),
        'rooms': '[]',
        'user_id': str(current_user.get_id()) if current_user.is_authenticated else 'anonymous'
    })


@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    current_app.logger.info(f"\nClient disconnecting: {sid}")

    conn_data = redis_hget(redis_client, "active_connections", sid) or {}
    current_app.logger.info(f"Connection data: {conn_data}")

    reservation_id = conn_data.get('reservation_id')

    if reservation_id:
        current_app.logger.info(f"Checking lease data for reservation: {reservation_id}")
        lease_data = redis_client.hgetall(f"lease_data:{reservation_id}")

        if lease_data:
            is_payment_lease = any(
                key in [b'stripe_session_id', 'stripe_session_id', b'payment_context', 'payment_context']
                for key in lease_data.keys())

            if is_payment_lease:
                current_app.logger.info(f"Payment lease detected - preserving {reservation_id}")
            else:
                current_app.logger.info(f"Cleaning up non-payment lease: {reservation_id}")
                try:
                    spot_id = lease_data.get(b'spot_id', b'').decode() if b'spot_id' in lease_data else lease_data.get('spot_id', '')
                    booking_date = lease_data.get(b'booking_date', b'').decode() if b'booking_date' in lease_data else lease_data.get('booking_date', '')

                    if spot_id and booking_date:
                        lease_key = f"spot_lease:{spot_id}_{booking_date}"
                        redis_safe_release_lease(redis_client, lease_key, reservation_id)
                        current_app.logger.info(f"Cleaned up lease {reservation_id} for spot {spot_id}")
                except Exception as e:
                    current_app.logger.error(f"Lease cleanup error: {str(e)}")
    else:
        current_app.logger.info("No reservation ID found in connection data")

    rooms_data = conn_data.get('rooms', '[]')
    try:
        if isinstance(rooms_data, str):
            rooms = json.loads(rooms_data)
        elif isinstance(rooms_data, list):
            rooms = rooms_data
        else:
            rooms = []
    except (json.JSONDecodeError, TypeError):
        rooms = []

    current_app.logger.info(f"Client was in {len(rooms)} rooms: {rooms}")

    if rooms:
        for room_name in rooms:
            if isinstance(room_name, str):
                redis_srem(redis_client, f"active_rooms:{room_name}", sid)
                if not redis_smembers(redis_client, f"active_rooms:{room_name}"):
                    redis_delete(redis_client, f"active_rooms:{room_name}")
                    current_app.logger.info(f"Deleted empty room: {room_name}")

    redis_hdel(redis_client, "active_connections", sid)
    current_app.logger.info(f"Removed connection data for sid: {sid}")


@socketio.on('book_spot')
def book_spot(data):
    from booking.booking_service import acquire_lease
    try:
        spot = ParkingSpot.query.get(data.get('spotId'))
        if not spot:
            emit('booking_failed', {'reason': 'Invalid spot'})
            return

        conn_data = redis_hget(redis_client, "active_connections", request.sid) or {}
        existing_reservation_id = conn_data.get('reservation_id')

        start_time_str = f"{data.get('startHour')}:{data.get('startMinute')}"
        end_time_str = f"{data.get('endHour')}:{data.get('endMinute')}"

        lease_key = f"spot_lease:{spot.id}_{data.get('bookingDate')}"

        reservation_id = acquire_lease(
            spot_id=spot.id,
            user_id=current_user.get_id(),
            parking_lot_id=data.get('parkingLotId'),
            booking_date=data.get('bookingDate'),
            start_time=start_time_str,
            end_time=end_time_str,
            reservation_id=existing_reservation_id
        )

        if not reservation_id:
            emit('booking_failed', {'reason': 'Spot already taken'})
            return

        conn_data['reservation_id'] = reservation_id
        redis_hset(redis_client, "active_connections", request.sid, conn_data)

        ok = emit_to_relevant_rooms_about_booking(
            spot,
            data.get('bookingDate'),
            False,
            True,
            datetime.strptime(start_time_str, "%H:%M").time(),
            datetime.strptime(end_time_str, "%H:%M").time()
        )

        checkout_url = create_stripe_session(
            data,
            start_time_str,
            end_time_str,
            spot,
            reservation_id
        )

        if not checkout_url:
            emit('booking_failed', {'reason': 'Payment system error'})
            lease_key = f"spot_lease:{spot.id}_{data.get('bookingDate')}"
            redis_safe_release_lease(redis_client, lease_key, reservation_id)
            emit_to_relevant_rooms_about_booking(spot, data.get('bookingDate'), True, False)
            return

        emit('payment_redirect', {'url': checkout_url})

    except Exception as e:
        current_app.logger.error(f"Booking failed: {str(e)}")
        emit('booking_failed', {'reason': str(e)})


@socketio.on('subscribe')
def handle_subscribe(data):
    try:
        parking_lot_id = data.get('parkingLotId')
        booking_date = data.get('bookingDate')
        start_time = data.get('startTime', '00:00')
        end_time = data.get('endTime', '23:59')

        if not parking_lot_id:
            print(f"Invalid subscription from {request.sid}: missing parkingLotId")
            emit('subscription_error', {'message': 'Missing parkingLotId'})
            return

        if not booking_date:
            print(f"Invalid subscription from {request.sid}: missing bookingDate")
            emit('subscription_error', {'message': 'Missing bookingDate'})
            return

        new_room_name = f"lot_{parking_lot_id}_{booking_date}"
        conn_data = redis_hget(redis_client, "active_connections", request.sid) or {}

        rooms_data = conn_data.get('rooms', '[]')
        try:
            if isinstance(rooms_data, str):
                current_rooms = json.loads(rooms_data)
            elif isinstance(rooms_data, list):
                current_rooms = rooms_data
            else:
                current_rooms = []
        except (json.JSONDecodeError, TypeError):
            current_rooms = []

        for room in current_rooms[:]:
            if isinstance(room, str) and room.startswith('lot_'):
                leave_room(room)
                redis_srem(redis_client, f"active_rooms:{room}", request.sid)
                if not redis_smembers(redis_client, f"active_rooms:{room}"):
                    redis_delete(redis_client, f"active_rooms:{room}")
                current_rooms.remove(room)

        join_room(new_room_name)
        redis_sadd(redis_client, f"active_rooms:{new_room_name}", request.sid)
        current_rooms.append(new_room_name)

        conn_data.update({
            'parkingLotId': str(parking_lot_id),
            'bookingDate': booking_date,
            'startTime': start_time,
            'endTime': end_time,
            'rooms': json.dumps(current_rooms)
        })

        redis_hset(redis_client, "active_connections", request.sid, conn_data)

        print(f"Client {request.sid} subscribed to {new_room_name} with times: {start_time}-{end_time}")

    except Exception as e:
        print(f"Subscription error for {request.sid}: {str(e)}")
        emit('subscription_error', {'message': 'Internal server error'})


def create_stripe_session(data, start_time_str, end_time_str, spot, reservation_id):
    """Create Stripe checkout session - mark lease as payment in progress"""
    try:
        lease_data_key = f"lease_data:{reservation_id}"
        redis_client.hset(lease_data_key, 'payment_context', 'true')
        redis_client.expire(lease_data_key, 600)

        start_time = datetime.strptime(start_time_str, "%H:%M").time()
        end_time = datetime.strptime(end_time_str, "%H:%M").time()
        hours = (end_time.hour - start_time.hour) + (end_time.minute - start_time.minute) / 60
        price = max(round(hours * 2 * 100), 50)

        success_url = f"{url_for('booking_bp.payment_success', _external=True)}?session_id={{CHECKOUT_SESSION_ID}}"
        cancel_url = url_for('booking_bp.booking_form', _external=True)

        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'eur',
                    'product_data': {
                        'name': f'Parking Spot #{spot.spotNumber}',
                        'description': f'{data.get("bookingDate")} {start_time_str}-{end_time_str}'
                    },
                    'unit_amount': price,
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                'reservation_id': reservation_id,
                'spot_id': str(spot.id),
                'parking_lot_id': data.get('parkingLotId'),
                'booking_date': data.get('bookingDate'),
                'start_time': start_time_str,
                'end_time': end_time_str,
                'user_id': str(current_user.get_id())
            }
        )

        redis_client.hset(lease_data_key, 'stripe_session_id', session.id)

        return session.url

    except Exception as e:
        current_app.logger.error(f"Stripe session creation failed: {str(e)}")
        return None


@booking_bp.route('/check_spot_availability', methods=['POST'])
def check_spot_availability():
    try:
        data = request.get_json()
        current_app.logger.info(f"DEBUG: Received data: {data}")

        parkingLotId = data.get('parkingLotId')
        startTime_str = data.get('startTime')
        endTime_str = data.get('endTime')
        bookingDate = data.get('bookingDate')

        startTime = datetime.strptime(startTime_str, "%H:%M").time()
        endTime = datetime.strptime(endTime_str, "%H:%M").time()

        current_app.logger.info(f"DEBUG: Checking lot {parkingLotId}, date {bookingDate}, time {startTime}-{endTime}")

        parkingLot = ParkingLot.query.get(parkingLotId)
        if not parkingLot:
            current_app.logger.error(f"Parking lot not found: {parkingLotId}")
            return jsonify({'error': 'Parking lot not found'}), 404

        allSpots = parkingLot.spots
        current_app.logger.info(f"Found {len(allSpots)} spots for parking lot {parkingLotId}")

        conflicting_bookings = Booking.query.filter(
            Booking.parking_lot_id == parkingLotId,
            Booking.bookingDate == bookingDate,
            Booking.startTime < endTime,
            Booking.endTime > startTime
        ).with_entities(Booking.spot_id).all()

        booked_spot_ids = {b[0] for b in conflicting_bookings}
        current_app.logger.info(f"Booked spot IDs: {booked_spot_ids}")

        lease_pattern = f"spot_lease:*_{bookingDate}"
        current_app.logger.info(f"Looking for lease pattern: {lease_pattern}")

        leased_spot_ids = set()
        cursor = 0
        lease_keys_found = []

        while True:
            cursor, keys = redis_client.scan(cursor=cursor, match=lease_pattern, count=100)
            current_app.logger.info(f"SCAN result - cursor: {cursor}, keys: {keys}")

            for lease_key in keys:
                if isinstance(lease_key, bytes):
                    lease_key = lease_key.decode('utf-8')

                lease_keys_found.append(lease_key)
                current_app.logger.info(f"Processing lease key: {lease_key}")

                try:
                    key_parts = lease_key.split(':')
                    if len(key_parts) < 2:
                        continue

                    spot_date_parts = key_parts[1].split('_')
                    if len(spot_date_parts) < 2:
                        continue

                    spot_id = spot_date_parts[0]

                    reservation_id = redis_client.get(lease_key)
                    if reservation_id and isinstance(reservation_id, bytes):
                        reservation_id = reservation_id.decode('utf-8')

                    current_app.logger.info(f"Lease {lease_key} -> spot {spot_id}, reservation {reservation_id}")

                    if reservation_id:
                        lease_data = redis_client.hgetall(f"lease_data:{reservation_id}")
                        current_app.logger.info(f"Lease data: {lease_data}")

                        if lease_data:
                            lease_start_str = lease_data.get(b'start_time', b'').decode() if b'start_time' in lease_data else lease_data.get('start_time', '')
                            lease_end_str = lease_data.get(b'end_time', b'').decode() if b'end_time' in lease_data else lease_data.get('end_time', '')

                            current_app.logger.info(f"Lease times - start: {lease_start_str}, end: {lease_end_str}")

                            if lease_start_str and lease_end_str:
                                lease_start = datetime.strptime(lease_start_str, "%H:%M").time()
                                lease_end = datetime.strptime(lease_end_str, "%H:%M").time()

                                base_date = datetime.today().date()
                                lease_start_dt = datetime.combine(base_date, lease_start)
                                lease_end_dt = datetime.combine(base_date, lease_end)
                                requested_start_dt = datetime.combine(base_date, startTime)
                                requested_end_dt = datetime.combine(base_date, endTime)

                                time_overlap = (
                                        (requested_start_dt < lease_end_dt) and
                                        (requested_end_dt > lease_start_dt)
                                )

                                current_app.logger.info(
                                    f"Time overlap check - requested: {startTime}-{endTime}, lease: {lease_start}-{lease_end}, overlap: {time_overlap}")

                                if time_overlap:
                                    leased_spot_ids.add(spot_id)
                                    current_app.logger.info(
                                        f"Added spot {spot_id} to leased spots due to time overlap")
                except (IndexError, ValueError, TypeError) as e:
                    current_app.logger.error(f"Error processing lease key {lease_key}: {e}")
                    continue

            if cursor == 0:
                break

        current_app.logger.info(f"Leased spot IDs: {leased_spot_ids}")
        current_app.logger.info(f"All lease keys found: {lease_keys_found}")

        spots_data = []
        for spot in allSpots:
            is_available = (spot.id not in booked_spot_ids and
                            str(spot.id) not in leased_spot_ids)

            current_app.logger.info(
                f"Spot {spot.id} - available: {is_available} (booked: {spot.id in booked_spot_ids}, leased: {str(spot.id) in leased_spot_ids})")

            spots_data.append({
                'id': spot.id,
                'spotNumber': spot.spotNumber,
                'svgCoords': spot.svgCoords,
                'is_available': is_available,
                'pricePerHour': spot.pricePerHour
            })

        return jsonify({
            'image_filename': parkingLot.image_filename,
            'spots': spots_data,
            'booked_count': len(booked_spot_ids),
            'leased_count': len(leased_spot_ids),
            'lease_keys_found': lease_keys_found
        })

    except Exception as e:
        current_app.logger.error(f"Error checking spot availability: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@booking_bp.route('/debug_redis', methods=['GET'])
def debug_redis():
    try:
        redis_ok = redis_client.ping()

        all_keys = redis_client.keys('*')

        lease_keys = redis_client.keys('spot_lease:*')
        lease_data_keys = redis_client.keys('lease_data:*')

        info = redis_client.info()

        return jsonify({
            'redis_connected': redis_ok,
            'total_keys': len(all_keys),
            'lease_keys': [k.decode('utf-8') if isinstance(k, bytes) else k for k in lease_keys],
            'lease_data_keys': [k.decode('utf-8') if isinstance(k, bytes) else k for k in lease_data_keys],
            'redis_info': {
                'used_memory': info.get('used_memory', 0),
                'connected_clients': info.get('connected_clients', 0),
                'total_commands_processed': info.get('total_commands_processed', 0)
            }
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500