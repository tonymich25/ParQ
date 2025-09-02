import threading
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
from .booking_service import acquire_lease, confirm_booking
from booking.redis import redis_sadd, redis_srem, redis_smembers, redis_hget, redis_hset, redis_delete, redis_hdel, \
    redis_delete_lease, redis_keys, redis_get
from booking.forms import BookingForm
from config import City, db, ParkingLot, Booking, ParkingSpot, app, socketio, redis_client, secrets

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
    from booking.booking_service import confirm_booking
    session_id = request.args.get('session_id')

    if not session_id:
        flash("Invalid payment session. Please try again.", "error")
        return redirect(url_for('booking_bp.booking_form'))

    try:
        session = stripe.checkout.Session.retrieve(session_id)
        reservation_id = session.metadata.get('reservation_id')
        spot_id = session.metadata.get('spot_id')
        parking_lot_id = session.metadata.get('parking_lot_id')
        booking_date = session.metadata.get('booking_date')
        start_time = session.metadata.get('start_time')
        end_time = session.metadata.get('end_time')

        if not all([reservation_id, spot_id, parking_lot_id, booking_date, start_time, end_time]):
            flash("Invalid payment session data. Please try again.", "error")
            return redirect(url_for('booking_bp.booking_form'))

        spot = ParkingSpot.query.get(spot_id)
        if not spot:
            flash("Invalid spot. Please try again.", "error")
            return redirect(url_for('booking_bp.booking_form'))

        # Prepare booking data for confirmation
        booking_data = {
            'parking_lot_id': parking_lot_id,
            'booking_date': booking_date,
            'start_time': start_time,
            'end_time': end_time
        }

        # Use idempotency key (Stripe session ID)
        idempotency_key = f"stripe_{session_id}"

        # Confirm the booking with atomic transaction
        result, status_code = confirm_booking(
            reservation_id=reservation_id,
            spot_id=spot_id,
            user_id=current_user.get_id(),
            booking_data=booking_data,
            idempotency_key=idempotency_key
        )

        if status_code != 200:
            # Booking failed - issue refund
            try:
                stripe.Refund.create(payment_intent=session.payment_intent)
                flash("Booking failed. Refund issued. Please try again.", "error")
            except stripe.error.StripeError:
                flash("Booking failed. Please contact support for refund.", "error")
            return redirect(url_for('booking_bp.booking_form'))

        # Booking successful
        booking_id = result.get('booking_id')
        if not booking_id:
            flash("Booking completed but could not retrieve booking details.", "warning")
            return redirect(url_for('dashboard.dashboard'))

        # Fetch the booking from database to generate QR code
        new_booking = Booking.query.get(booking_id)
        if not new_booking:
            flash("Booking completed but details not found.", "warning")
            return redirect(url_for('dashboard.dashboard'))

        generate_qr_code(new_booking.id)
        disconnect_user(session)

        flash("Your booking and payment were successful!", "success")
        return redirect(url_for('dashboard.dashboard'))

    except stripe.error.StripeError as e:
        current_app.logger.error(f"Stripe error in payment_success: {str(e)}")
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

    # Iterate through all active rooms using Redis pattern matching
    room_keys = redis_keys("active_rooms:*")

    for room_key in room_keys:
        room_name = room_key.replace("active_rooms:", "")
        sids = redis_smembers(room_key)

        # Check if any socket in this room belongs to the user
        user_sids = {sid for sid in sids if sid.startswith(f"{user_id}_")}

        for sid in user_sids:
            emit('payment_complete', {}, room=sid)
            socketio.disconnect(sid)
            # Remove from room
            redis_srem(room_key, sid)


def emit_to_relevant_rooms_about_booking(spot, booking_date, is_available, return_confirmation, start_time=None,
                                         end_time=None):
    try:
        target_room = f"lot_{spot.parkingLotId}_{booking_date}"
        print(f"\n=== Starting emission to {target_room} ===")
        print(f"Spot: {spot.id} | Available: {is_available} | Time Range: {start_time}-{end_time}")

        # Check if room exists using Redis
        room_key = f"active_rooms:{target_room}"
        sids = redis_smembers(room_key)
        if not sids:
            print(f"Room {target_room} not found")
            return False if return_confirmation else None

        # Convert input times
        if isinstance(start_time, str) and start_time:
            start_time = datetime.strptime(start_time, "%H:%M").time()
        if isinstance(end_time, str) and end_time:
            end_time = datetime.strptime(end_time, "%H:%M").time()

        recipients = 0
        for sid in sids:
            # Get connection data from Redis
            conn_data = redis_hget("active_connections", sid)
            if not conn_data:
                print(f"Missing connection data for {sid}")
                continue

            # Get client's time range with validation
            try:
                conn_start_str = conn_data.get('startTime')
                conn_end_str = conn_data.get('endTime')
                conn_start = datetime.strptime(conn_start_str, "%H:%M").time() if conn_start_str else None
                conn_end = datetime.strptime(conn_end_str, "%H:%M").time() if conn_end_str else None
            except (ValueError, TypeError) as e:
                print(f"Invalid time format for {sid}: {e}")
                continue

            # Determine if we should send the update
            send_update = True
            if start_time and end_time and conn_start and conn_end:
                # Check for time overlap
                time_overlap = not (end_time <= conn_start or start_time >= conn_end)
                send_update = time_overlap
                print(f"Client {sid} | Times: {conn_start_str}-{conn_end_str} | Overlap: {time_overlap}")

            if send_update:
                socketio.emit('spot_update', {
                    'spotId': spot.id,
                    'available': is_available,
                    'timestamp': datetime.now(ZoneInfo("Europe/Nicosia")).isoformat()
                }, room=sid)
                recipients += 1

        print(f"=== Emission complete === Recipients: {recipients}\n")
        return True

    except Exception as e:
        print(f"Emission error: {str(e)}")
        return False


@socketio.on('connect')
def handle_connect():
    print("Client connected: ", request.sid)
    # Store connection info in Redis hash
    redis_hset("active_connections", request.sid, {
        'connected_at': datetime.now(ZoneInfo("Europe/Nicosia")).isoformat(),
        'rooms': '[]',
        'user_id': str(current_user.get_id()) if current_user.is_authenticated else 'anonymous'
    })


@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    print(f"\nClient disconnecting: {sid}")

    # Clean up from all rooms using Redis
    conn_data = redis_hget("active_connections", sid) or {}

    # Parse rooms from JSON with proper error handling
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

    if rooms:
        for room_name in rooms:
            if isinstance(room_name, str):
                redis_srem(f"active_rooms:{room_name}", sid)
                # Clean up empty rooms
                if not redis_smembers(f"active_rooms:{room_name}"):
                    redis_delete(f"active_rooms:{room_name}")
                print(f"Removed from room: {room_name}")

    # Clean up connection data from Redis
    redis_hdel("active_connections", sid)
    print(f"Disconnect complete for {sid}\n")


def calculate_price(startTime, endTime, spotPricePerHour):
    # Create datetime objects combining today's date with the times
    start_dt = datetime.combine(datetime.today().date(), startTime)
    end_dt = datetime.combine(datetime.today().date(), endTime)

    # Calculate duration in hours
    duration_hours = (end_dt - start_dt).total_seconds() / 3600

    # Calculate price in cents and ensure minimum charge
    price_cents = int(round(duration_hours * spotPricePerHour * 100))
    return max(price_cents, 50)  # Ensure minimum charge of 50 cents


def create_stripe_session(data, startTimeStr, endTimeStr, spot, reservation_id):
    try:
        # Convert strings to datetime objects for price calculation
        start_time = datetime.strptime(startTimeStr, "%H:%M").time()
        end_time = datetime.strptime(endTimeStr, "%H:%M").time()

        # Calculate price
        price_cents = calculate_price(start_time, end_time, spot.pricePerHour)

        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'eur',
                    'product_data': {
                        'name': f'Parking Spot #{spot.spotNumber}',
                        'description': f"Parking from {startTimeStr} to {endTimeStr}",
                    },
                    'unit_amount': price_cents,
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=url_for('booking_bp.payment_success', _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=url_for('booking_bp.booking_form', _external=True),
            metadata={
                'user_id': str(current_user.get_id()),
                'spot_id': str(data.get('spotId')),
                'parking_lot_id': str(data.get('parkingLotId')),
                'booking_date': data.get('bookingDate'),
                'start_time': startTimeStr,
                'end_time': endTimeStr,
                'reservation_id': reservation_id
            }
        )
        return checkout_session.url
    except Exception as e:
        current_app.logger.error(f"Stripe session creation failed: {str(e)}", exc_info=True)
        return None


@socketio.on('book_spot')
def book_spot(data):
    from booking.booking_service import acquire_lease
    try:
        spot = ParkingSpot.query.get(data.get('spotId'))
        if not spot:
            emit('booking_failed', {'reason': 'Invalid spot'})
            return

        start_time_str = f"{data.get('startHour')}:{data.get('startMinute')}"
        end_time_str = f"{data.get('endHour')}:{data.get('endMinute')}"

        # Convert to time objects for database operations
        start_time = datetime.strptime(start_time_str, "%H:%M").time()
        end_time = datetime.strptime(end_time_str, "%H:%M").time()

        # Use new leasing system instead of old spot_holds
        reservation_id = acquire_lease(
            spot_id=spot.id,
            user_id=current_user.get_id(),
            parking_lot_id=data.get('parkingLotId'),
            booking_date=data.get('bookingDate'),
            start_time=start_time_str,
            end_time=end_time_str
        )

        if not reservation_id:
            emit('booking_failed', {'reason': 'Spot already taken'})
            return

        # Emit update to make spot appear taken
        ok = emit_to_relevant_rooms_about_booking(
            spot,
            data.get('bookingDate'),
            False,
            True,
            start_time,
            end_time
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
            # Release the lease if payment fails
            lease_key = f"spot_lease:{spot.id}"
            redis_delete_lease(lease_key, reservation_id)
            emit_to_relevant_rooms_about_booking(spot, data.get('bookingDate'), True, False)
            return

        emit('payment_redirect', {'url': checkout_url})

        # Store reservation_id in connection data for later retrieval
        conn_data = redis_hget("active_connections", request.sid) or {}
        conn_data['reservation_id'] = reservation_id
        redis_hset("active_connections", request.sid, conn_data)

    except Exception as e:
        current_app.logger.error(f"Booking failed: {str(e)}")
        emit('booking_failed', {'reason': str(e)})


def is_spot_available(spot, parkingLotId, bookingDate, startTime, endTime):
    # Check Redis lease first (new system)
    lease_key = f"spot_lease:{spot.id}"
    current_lease = redis_get(lease_key)

    # If there's an active lease, spot is not available
    if current_lease:
        return 1

    # Check existing bookings
    return Booking.query.filter(
        Booking.spot_id == spot.id,
        Booking.parking_lot_id == parkingLotId,
        Booking.bookingDate == bookingDate,
        Booking.startTime < endTime,
        Booking.endTime > startTime
    ).count()


def release_spot_if_unpaid(spot_id, bookingDate):
    """Legacy function - will be removed after migration"""
    pass


@socketio.on('subscribe')
def handle_subscribe(data):
    try:
        parking_lot_id = data.get('parkingLotId')
        booking_date = data.get('bookingDate')
        start_time = data.get('startTime')
        end_time = data.get('endTime')
        if not parking_lot_id or not booking_date:
            print("Invalid subscription: missing required fields")
            return
        new_room_name = f"lot_{parking_lot_id}_{booking_date}"
        conn_data = redis_hget(redis_client, "active_connections", request.sid) or {}  # ADD redis_client
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
        if new_room_name in current_rooms:
            conn_data.update({
                'startTime': start_time,
                'endTime': end_time
            })
            redis_hset(redis_client, "active_connections", request.sid, conn_data)  # ADD redis_client
        else:
            for room in current_rooms:
                if isinstance(room, str) and room.startswith('lot_'):
                    leave_room(room)
                    redis_srem(redis_client, f"active_rooms:{room}", request.sid)  # ADD redis_client
                    if not redis_smembers(redis_client, f"active_rooms:{room}"):  # ADD redis_client
                        redis_delete(redis_client, f"active_rooms:{room}")  # ADD redis_client
            join_room(new_room_name)
            redis_sadd(redis_client, f"active_rooms:{new_room_name}", request.sid)  # ADD redis_client
            current_rooms.append(new_room_name)
            conn_data.update({
                'parkingLotId': str(parking_lot_id),
                'bookingDate': booking_date,
                'startTime': start_time,
                'endTime': end_time,
                'rooms': json.dumps(current_rooms)
            })
            redis_hset(redis_client, "active_connections", request.sid, conn_data)  # ADD redis_client
    except Exception as e:
        print(f"Subscription error for {request.sid}: {str(e)}")


@socketio.on('subscribe')
def handle_subscribe(data):
    try:
        parking_lot_id = data.get('parkingLotId')
        booking_date = data.get('bookingDate')
        start_time = data.get('startTime')
        end_time = data.get('endTime')

        if not parking_lot_id or not booking_date:
            print("Invalid subscription: missing required fields")
            return

        new_room_name = f"lot_{parking_lot_id}_{booking_date}"

        # Get current rooms from Redis connection data with proper JSON handling
        conn_data = redis_hget("active_connections", request.sid) or {}

        # Parse rooms from JSON
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

        if new_room_name in current_rooms:
            # Same room: just update time range
            conn_data.update({
                'startTime': start_time,
                'endTime': end_time
            })
            # Update connection data in Redis
            redis_hset("active_connections", request.sid, conn_data)
            print(f"Client {request.sid} updated time range in existing room {new_room_name} ({start_time}-{end_time})")
        else:
            # Leave old lot rooms
            for room in current_rooms:
                if isinstance(room, str) and room.startswith('lot_'):
                    leave_room(room)
                    # Remove from room using Redis
                    redis_srem(f"active_rooms:{room}", request.sid)
                    # Clean up empty rooms
                    if not redis_smembers(f"active_rooms:{room}"):
                        redis_delete(f"active_rooms:{room}")

            # Join new room
            join_room(new_room_name)
            # Add to room using Redis
            redis_sadd(f"active_rooms:{new_room_name}", request.sid)

            # Update connection info in Redis
            current_rooms.append(new_room_name)
            conn_data.update({
                'parkingLotId': str(parking_lot_id),
                'bookingDate': booking_date,
                'startTime': start_time,
                'endTime': end_time,
                'rooms': json.dumps(current_rooms)  # Store as JSON string
            })
            redis_hset("active_connections", request.sid, conn_data)
            print(f"Client {request.sid} subscribed to new room {new_room_name} ({start_time}-{end_time})")

    except Exception as e:
        print(f"Subscription error for {request.sid}: {str(e)}")


@booking_bp.route('/check_spot_availability', methods=['POST'])
def check_spot_availability():
    try:
        data = request.get_json()
        parkingLotId = data.get('parkingLotId')
        startTime_str = data.get('startTime')
        endTime_str = data.get('endTime')
        bookingDate = data.get('bookingDate')

        # Convert times
        startTime = datetime.strptime(startTime_str, "%H:%M").time()
        endTime = datetime.strptime(endTime_str, "%H:%M").time()

        parkingLot = ParkingLot.query.get(parkingLotId)
        if not parkingLot:
            return jsonify({'error': 'Parking lot not found'}), 404

        now = datetime.now(ZoneInfo("Europe/Nicosia"))
        allSpots = parkingLot.spots

        # Get all active leases from Redis (new system)
        active_leases = {}
        # Use Redis pattern matching to find all spot leases
        lease_keys = redis_keys("spot_lease:*")
        for lease_key in lease_keys:
            spot_id = lease_key.replace("spot_lease:", "")
            reservation_id = redis_get(lease_key)
            if reservation_id:
                active_leases[spot_id] = {'reservation_id': reservation_id}

        # Get conflicting bookings
        conflicting_bookings = Booking.query.filter(
            Booking.parking_lot_id == parkingLotId,
            Booking.bookingDate == bookingDate,
            Booking.startTime < endTime,
            Booking.endTime > startTime
        ).with_entities(Booking.spot_id).all()

        booked_spot_ids = {b[0] for b in conflicting_bookings}
        leased_spot_ids = set(active_leases.keys())

        spots_data = []
        for spot in allSpots:
            is_available = (spot.id not in booked_spot_ids and
                            str(spot.id) not in leased_spot_ids)

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
            'leases': list(leased_spot_ids)  # Return leased spots for UI
        })

    except Exception as e:
        current_app.logger.error(f"Error checking spot availability: {str(e)}")
        return jsonify({'error': str(e)}), 500