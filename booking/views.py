import json
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
import qrcode
import redis
import stripe
from cryptography.fernet import Fernet
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from flask_socketio import join_room, emit
from flask_socketio import leave_room

from booking import booking_service
from booking.booking_service import acquire_lease, confirm_booking, redis_circuit_open, acquire_lease_safe
from booking.db_utils import is_spot_available_in_db
from booking.forms import BookingForm
from booking.redis_utils import redis_sadd, redis_srem, redis_smembers, redis_hget, redis_hset, redis_delete, \
    redis_hdel, \
    redis_keys, redis_safe_release_lease
from booking.utils import emit_to_relevant_rooms_about_booking, calculate_price, get_pending_booking, store_pending_booking, delete_pending_booking
from config import City, db, ParkingLot, Booking, ParkingSpot, app, socketio, redis_client, secrets, PendingBooking

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
    current_app.logger.info(f"💰 payment_success called with session_id: {session_id}")

    if not session_id:
        current_app.logger.error("❌ No session_id provided in payment_success")
        flash("Invalid payment session. Please try again.", "error")
        return redirect(url_for('booking_bp.booking_form'))

    try:
        # Retrieve Stripe session
        current_app.logger.info(f"🔍 Retrieving Stripe session: {session_id}")
        session = stripe.checkout.Session.retrieve(session_id)
        current_app.logger.info(f"✅ Stripe session retrieved: {session.id}, status: {session.payment_status}")

        # Extract metadata
        reservation_id = session.metadata.get('reservation_id')
        spot_id = session.metadata.get('spot_id')
        parking_lot_id = session.metadata.get('parking_lot_id')
        booking_date = session.metadata.get('booking_date')
        start_time = session.metadata.get('start_time')
        end_time = session.metadata.get('end_time')
        user_id = session.metadata.get('user_id')

        current_app.logger.info(f"📋 Session metadata - reservation_id: {reservation_id}, spot_id: {spot_id}, "
                              f"parking_lot_id: {parking_lot_id}, booking_date: {booking_date}, "
                              f"start_time: {start_time}, end_time: {end_time}, user_id: {user_id}")

        # Validate all required metadata
        if not all([reservation_id, spot_id, parking_lot_id, booking_date, start_time, end_time, user_id]):
            current_app.logger.error("❌ Missing required metadata in Stripe session")
            flash("Invalid payment session data. Please try again.", "error")
            return redirect(url_for('booking_bp.booking_form'))

        # Verify spot exists
        current_app.logger.info(f"🔍 Verifying spot exists: {spot_id}")
        spot = ParkingSpot.query.get(spot_id)
        if not spot:
            current_app.logger.error(f"❌ Spot not found: {spot_id}")
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
        current_app.logger.info(f"🎯 Using idempotency key: {idempotency_key}")

        # Confirm the booking with atomic transaction
        current_app.logger.info(f"✅ Attempting to confirm booking for reservation: {reservation_id}")
        result, status_code = confirm_booking(
            reservation_id=reservation_id,
            spot_id=spot_id,
            user_id=user_id,
            booking_data=booking_data,
            idempotency_key=idempotency_key
        )

        current_app.logger.info(f"📊 Booking confirmation result: {result}, status_code: {status_code}")

        if status_code != 200:
            # Booking failed - issue refund
            current_app.logger.error(f"❌ Booking failed with status {status_code}. Issuing refund.")
            try:
                refund = stripe.Refund.create(payment_intent=session.payment_intent)
                current_app.logger.info(f"💸 Refund issued: {refund.id}")
                flash("Booking failed. Refund issued. Please try again.", "error")
            except stripe.error.StripeError as refund_error:
                current_app.logger.error(f"❌ Refund failed: {str(refund_error)}")
                flash("Booking failed. Please contact support for refund.", "error")
            return redirect(url_for('booking_bp.booking_form'))

        # Booking successful
        booking_id = result.get('booking_id')
        current_app.logger.info(f"🎉 Booking successful! Booking ID: {booking_id}")

        if not booking_id:
            current_app.logger.warning("⚠️ Booking completed but no booking_id returned")
            flash("Booking completed but could not retrieve booking details.", "warning")
            return redirect(url_for('dashboard.dashboard'))

        # Fetch the booking from database to generate QR code
        current_app.logger.info(f"🔍 Fetching booking from database: {booking_id}")
        new_booking = Booking.query.get(booking_id)
        if not new_booking:
            current_app.logger.warning(f"⚠️ Booking not found in database: {booking_id}")
            flash("Booking completed but details not found.", "warning")
            return redirect(url_for('dashboard.dashboard'))

        # Generate QR code
        current_app.logger.info("📱 Generating QR code")
        generate_qr_code(new_booking.id)

        # Disconnect user sockets (but preserve lease until cleanup)
        current_app.logger.info("🔌 Disconnecting user sockets")
        disconnect_user(session)

        current_app.logger.info("✅ Payment and booking process completed successfully!")
        flash("Your booking and payment were successful!", "success")
        return redirect(url_for('dashboard.dashboard'))

    except stripe.error.StripeError as e:
        current_app.logger.error(f"❌ Stripe error in payment_success: {str(e)}", exc_info=True)
        flash("Payment processing error. Please contact support.", "error")
        return redirect(url_for('booking_bp.booking_form'))
    except Exception as e:
        current_app.logger.error(f"❌ Unexpected error in payment_success: {str(e)}", exc_info=True)
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
    current_app.logger.info(f"🔌 disconnect_user called for user_id: {user_id}")

    # Iterate through all active rooms using Redis pattern matching
    room_keys = redis_keys(redis_client, "active_rooms:*")
    current_app.logger.info(f"🔍 Found {len(room_keys)} active rooms")

    for room_key in room_keys:
        room_name = room_key.replace("active_rooms:", "")
        sids = redis_smembers(redis_client, room_key)
        current_app.logger.info(f"👥 Room {room_name} has {len(sids)} connections")

        # Check if any socket in this room belongs to the user
        user_sids = {sid for sid in sids if sid.startswith(f"{user_id}_")}
        current_app.logger.info(f"👤 User {user_id} has {len(user_sids)} connections in room {room_name}")

        for sid in user_sids:
            # Check if this connection has an active payment lease
            conn_data = redis_hget("active_connections", sid) or {}
            reservation_id = conn_data.get('reservation_id')

            if reservation_id:
                # Check if this is a payment-related lease
                lease_data = redis_client.hgetall(f"lease_data:{reservation_id}")
                if lease_data and any(key in [b'stripe_session_id', 'stripe_session_id'] for key in lease_data.keys()):
                    current_app.logger.info(f"💰 Preserving payment lease {reservation_id} for sid {sid}")
                    continue  # Skip cleanup for payment leases

            current_app.logger.info(f"🚫 Disconnecting sid {sid} from room {room_name}")
            emit('payment_complete', {}, room=sid)
            socketio.disconnect(sid)
            # Remove from room
            redis_srem(room_key, sid)


# @socketio.on('connect')
# def handle_connect():
#     print("Client connected: ", request.sid)
#     # Store connection info in Redis hash
#     redis_hset("active_connections", request.sid, {
#         'connected_at': datetime.now(ZoneInfo("Europe/Nicosia")).isoformat(),
#         'rooms': '[]',
#         'user_id': str(current_user.get_id()) if current_user.is_authenticated else 'anonymous'
#     })


#def disconnect_user(session):
#    user_id = session.metadata['user_id']
#    room_keys = redis_keys(redis_client, "active_rooms:*")  # ADD redis_client
#    for room_key in room_keys:
#        room_name = room_key.replace("active_rooms:", "")
#        sids = redis_smembers(redis_client, room_key)  # ADD redis_client
#        user_sids = {sid for sid in sids if sid.startswith(f"{user_id}_")}
#        for sid in user_sids:
#            emit('payment_complete', {}, room=sid)
#            socketio.disconnect(sid)
#            redis_srem(redis_client, room_key, sid)  # ADD redis_client

@socketio.on('connect')
def handle_connect():
    print("Client connected: ", request.sid)
    redis_hset(redis_client, "active_connections", request.sid, {  # ADD redis_client
        'connected_at': datetime.now(ZoneInfo("Europe/Nicosia")).isoformat(),
        'rooms': '[]',
        'user_id': str(current_user.get_id()) if current_user.is_authenticated else 'anonymous'
    })


@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    current_app.logger.info(f"\n🔌 Client disconnecting: {sid}")

    room_keys = redis_keys(redis_client, "active_rooms:*")

    conn_data = redis_hget(redis_client, "active_connections", sid) or {}
    current_app.logger.info(f"📋 Connection data: {conn_data}")

    reservation_id = conn_data.get('reservation_id')

    if reservation_id:
        current_app.logger.info(f"🔍 Checking lease data for reservation: {reservation_id}")
        lease_data = redis_client.hgetall(f"lease_data:{reservation_id}")

        if lease_data:
            # Check if this is a payment-related lease
            is_payment_lease = any(key in [b'stripe_session_id', 'stripe_session_id', b'payment_context', 'payment_context']
                                 for key in lease_data.keys())

            if is_payment_lease:
                current_app.logger.info(f"💰 Payment lease detected - preserving {reservation_id}")
                # DON'T cleanup - the payment_success route will handle cleanup
            else:
                current_app.logger.info(f"🗑️ Cleaning up non-payment lease: {reservation_id}")
                try:
                    spot_id = lease_data.get(b'spot_id', b'').decode() if b'spot_id' in lease_data else lease_data.get('spot_id', '')
                    booking_date = lease_data.get(b'booking_date', b'').decode() if b'booking_date' in lease_data else lease_data.get('booking_date', '')

                    if spot_id and booking_date:
                        lease_key = f"spot_lease:{spot_id}_{booking_date}"
                        redis_safe_release_lease(redis_client, lease_key, reservation_id)
                        current_app.logger.info(f"✅ Cleaned up lease {reservation_id} for spot {spot_id}")
                except Exception as e:
                    current_app.logger.error(f"❌ Lease cleanup error: {str(e)}")
    else:
        current_app.logger.info("ℹ️ No reservation ID found in connection data")

    # Original disconnect logic
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

    current_app.logger.info(f"🚪 Client was in {len(rooms)} rooms: {rooms}")

    if rooms:
        for room_name in rooms:
            if isinstance(room_name, str):
                redis_srem(redis_client, f"active_rooms:{room_name}", sid)
                if not redis_smembers(redis_client, f"active_rooms:{room_name}"):
                    redis_delete(redis_client, f"active_rooms:{room_name}")
                    current_app.logger.info(f"🗑️ Deleted empty room: {room_name}")

    redis_hdel(redis_client, "active_connections", sid)
    current_app.logger.info(f"✅ Removed connection data for sid: {sid}")


@socketio.on('book_spot')
def book_spot(data):
    try:
        current_app.logger.info(f"🎯 book_spot event received: {data}")
        current_app.logger.info(f"🔌 Circuit breaker status: {booking_service.redis_circuit_open}")

        # Try Redis-based booking first
        try:
            return process_redis_booking(data, request.sid)
        except (redis.exceptions.ConnectionError, Exception) as e:
            if isinstance(e, redis.exceptions.ConnectionError):
                # Set circuit breaker and fallback to direct booking
                booking_service.redis_circuit_open = True
                current_app.logger.warning("🔴 Redis connection failed - opening circuit breaker")

            current_app.logger.warning(f"🔄 Redis booking failed, falling back to direct: {str(e)}")
            return process_direct_booking(data, request.sid)

    except Exception as e:
        current_app.logger.error(f"❌ book_spot error: {str(e)}", exc_info=True)
        emit('booking_failed', {'reason': 'Booking failed'}, room=request.sid)


def process_redis_booking(data, sid):
    """Process booking using Redis lease system"""
    try:
        spot = ParkingSpot.query.get(data.get('spotId'))
        if not spot:
            emit('booking_failed', {'reason': 'Invalid spot'}, room=sid)
            return

        # Get existing reservation ID for idempotency
        conn_data = redis_hget(redis_client, "active_connections", sid) or {}
        existing_reservation_id = conn_data.get('reservation_id')

        start_time_str = f"{data.get('startHour')}:{data.get('startMinute')}"
        end_time_str = f"{data.get('endHour')}:{data.get('endMinute')}"

        # ✅ Redis connection check - this will raise ConnectionError if Redis is down
        redis_client.ping()

        reservation_id = acquire_lease_safe(
            spot_id=spot.id,
            user_id=current_user.get_id(),
            parking_lot_id=data.get('parkingLotId'),
            booking_date=data.get('bookingDate'),
            start_time=start_time_str,
            end_time=end_time_str,
            reservation_id=existing_reservation_id
        )

        if not reservation_id:
            emit('booking_failed', {'reason': 'Spot already taken'}, room=sid)
            return

        # Store reservation
        conn_data['reservation_id'] = reservation_id
        redis_hset(redis_client, "active_connections", sid, conn_data)

        # Emit spot update
        emit_to_relevant_rooms_about_booking(
            spot,
            data.get('bookingDate'),
            False,  # available=False
            True,  # return_confirmation=True
            datetime.strptime(start_time_str, "%H:%M").time(),
            datetime.strptime(end_time_str, "%H:%M").time()
        )

        # Create Stripe session
        checkout_url = create_stripe_session(
            data, start_time_str, end_time_str, spot, reservation_id
        )

        if not checkout_url:
            # Cleanup on failure
            lease_key = f"spot_lease:{spot.id}_{data.get('bookingDate')}"
            redis_safe_release_lease(redis_client, lease_key, reservation_id)
            emit_to_relevant_rooms_about_booking(
                spot, data.get('bookingDate'), True, False
            )
            emit('booking_failed', {'reason': 'Payment system error'}, room=sid)
            return

        # Success
        emit('payment_redirect', {'url': checkout_url}, room=sid)

    except redis.exceptions.ConnectionError:
        # Re-raise to trigger fallback
        raise
    except Exception as e:
        current_app.logger.error(f"❌ Redis booking error: {str(e)}")
        emit('booking_failed', {'reason': str(e)}, room=sid)

@socketio.on('subscribe')
def handle_subscribe(data):
    try:
        parking_lot_id = data.get('parkingLotId')
        booking_date = data.get('bookingDate')
        start_time = data.get('startTime', '00:00')
        end_time = data.get('endTime', '23:59')

        # 🎯 FIX: Better validation with proper error messages
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

        # Handle rooms data
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

        # Leave all existing parking lot rooms
        for room in current_rooms[:]:  # Create a copy to avoid modification during iteration
            if isinstance(room, str) and room.startswith('lot_'):
                leave_room(room)
                redis_srem(redis_client, f"active_rooms:{room}", request.sid)
                if not redis_smembers(redis_client, f"active_rooms:{room}"):
                    redis_delete(redis_client, f"active_rooms:{room}")
                current_rooms.remove(room)

        # Join new room
        join_room(new_room_name)
        redis_sadd(redis_client, f"active_rooms:{new_room_name}", request.sid)
        current_rooms.append(new_room_name)

        # Update connection data
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
        # 🎯 FIX: Add payment context to lease data
        lease_data_key = f"lease_data:{reservation_id}"
        # Store the Stripe session ID in the lease data
        redis_hset(redis_client, lease_data_key, 'payment_context', 'true')
        # Extend TTL for payment process (10 minutes)
        redis_client.expire(lease_data_key, 600)

        # Calculate price
        start_time = datetime.strptime(start_time_str, "%H:%M").time()
        end_time = datetime.strptime(end_time_str, "%H:%M").time()
        hours = (end_time.hour - start_time.hour) + (end_time.minute - start_time.minute) / 60
        price = max(round(hours * 2 * 100), 50)

        # Create Stripe session
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

        # 🎯 Store the session ID in lease data
        redis_client.hset(lease_data_key, 'stripe_session_id', session.id)

        return session.url

    except Exception as e:
        current_app.logger.error(f"Stripe session creation failed: {str(e)}")
        return None


@booking_bp.route('/check_spot_availability', methods=['POST'])
def check_spot_availability():
    try:
        data = request.get_json()
        current_app.logger.info(f"🔍 DEBUG: Received data: {data}")

        parkingLotId = data.get('parkingLotId')
        startTime_str = data.get('startTime')
        endTime_str = data.get('endTime')
        bookingDate = data.get('bookingDate')

        # Convert times
        startTime = datetime.strptime(startTime_str, "%H:%M").time()
        endTime = datetime.strptime(endTime_str, "%H:%M").time()

        current_app.logger.info(f"🔍 DEBUG: Checking lot {parkingLotId}, date {bookingDate}, time {startTime}-{endTime}")

        parkingLot = ParkingLot.query.get(parkingLotId)
        if not parkingLot:
            current_app.logger.error(f"❌ Parking lot not found: {parkingLotId}")
            return jsonify({'error': 'Parking lot not found'}), 404

        allSpots = parkingLot.spots
        current_app.logger.info(f"🔍 Found {len(allSpots)} spots for parking lot {parkingLotId}")

        # 🎯 FIX: CORRECT time comparison in SQL query
        conflicting_bookings = Booking.query.filter(
            Booking.parking_lot_id == parkingLotId,
            Booking.bookingDate == bookingDate,
            Booking.startTime < endTime,   # CORRECT: Booking starts before our end time
            Booking.endTime > startTime    # CORRECT: Booking ends after our start time
        ).with_entities(Booking.spot_id).all()

        booked_spot_ids = {b[0] for b in conflicting_bookings}
        current_app.logger.info(f"🔍 Booked spot IDs: {booked_spot_ids}")

        # 🎯 FIX: Debug Redis key lookup
        lease_pattern = f"spot_lease:*_{bookingDate}"
        current_app.logger.info(f"🔍 Looking for lease pattern: {lease_pattern}")

        # Use SCAN instead of KEYS for better performance
        leased_spot_ids = set()
        lease_keys_found = []
        cursor = 0
        try:
            while True:
                cursor, keys = redis_client.scan(cursor=cursor, match=lease_pattern, count=100)
                current_app.logger.info(f"🔍 SCAN result - cursor: {cursor}, keys: {keys}")

                for lease_key in keys:
                    if isinstance(lease_key, bytes):
                        lease_key = lease_key.decode('utf-8')

                    lease_keys_found.append(lease_key)
                    current_app.logger.info(f"🔍 Processing lease key: {lease_key}")

                    try:
                        # Extract spot_id from key format: "spot_lease:{spot_id}_{date}"
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

                        current_app.logger.info(f"🔍 Lease {lease_key} -> spot {spot_id}, reservation {reservation_id}")

                        if reservation_id:
                            # Get lease metadata to check time overlap
                            lease_data = redis_client.hgetall(f"lease_data:{reservation_id}")
                            current_app.logger.info(f"🔍 Lease data: {lease_data}")

                            if lease_data:
                                # Handle Redis bytes data
                                lease_start_str = lease_data.get(b'start_time', b'').decode() if b'start_time' in lease_data else lease_data.get('start_time', '')
                                lease_end_str = lease_data.get(b'end_time', b'').decode() if b'end_time' in lease_data else lease_data.get('end_time', '')

                                current_app.logger.info(f"🔍 Lease times - start: {lease_start_str}, end: {lease_end_str}")

                                if lease_start_str and lease_end_str:
                                    lease_start = datetime.strptime(lease_start_str, "%H:%M").time()
                                    lease_end = datetime.strptime(lease_end_str, "%H:%M").time()

                                    # 🎯 FIX: PROPER TIME OVERLAP LOGIC
                                    # Convert to datetime for proper comparison (handle edge cases)
                                    base_date = datetime.today().date()
                                    lease_start_dt = datetime.combine(base_date, lease_start)
                                    lease_end_dt = datetime.combine(base_date, lease_end)
                                    requested_start_dt = datetime.combine(base_date, startTime)
                                    requested_end_dt = datetime.combine(base_date, endTime)

                                    # Check if time ranges overlap (exclusive of endpoints)
                                    time_overlap = (
                                        (requested_start_dt < lease_end_dt) and
                                        (requested_end_dt > lease_start_dt)
                                    )

                                    app.logger.info(f"🔍 Time overlap check - requested: {startTime}-{endTime}, lease: {lease_start}-{lease_end}, overlap: {time_overlap}")

                                    if time_overlap:
                                        leased_spot_ids.add(spot_id)
                                        current_app.logger.info(f"🔍 Added spot {spot_id} to leased spots due to time overlap")
                    except (IndexError, ValueError, TypeError) as e:
                        current_app.logger.error(f"❌ Error processing lease key {lease_key}: {e}")
                        continue

                if cursor == 0:
                    break

        except redis.exceptions.ConnectionError:

            # ✅ CIRCUIT BREAKER ACTIVATED: Redis is down
            current_app.logger.warning("✅ Circuit Breaker: Redis down. Using DB results only.")
            # We continue with leased_spot_ids as an empty set - no leased spots will be considered
            leased_spot_ids = set()

        except Exception as e:
            # For any other Redis errors, also use the fallback
            current_app.logger.error(f"❌ Redis error (using fallback): {e}")
            leased_spot_ids = set()


        current_app.logger.info(f"🔍 Leased spot IDs: {leased_spot_ids}")
        current_app.logger.info(f"🔍 All lease keys found: {lease_keys_found}")

        pending_conflicts = PendingBooking.query.filter(
            PendingBooking.parking_lot_id == parkingLotId,
            PendingBooking.booking_date == bookingDate,
            PendingBooking.start_time < endTime,
            PendingBooking.end_time > startTime
        ).with_entities(PendingBooking.spot_id).all()

        pending_spot_ids = {p[0] for p in pending_conflicts}
        current_app.logger.info(f"🔍 Pending booking spot IDs: {pending_spot_ids}")



        spots_data = []
        for spot in allSpots:
            is_available = (spot.id not in booked_spot_ids and
                           str(spot.id) not in leased_spot_ids and
                           spot.id not in pending_spot_ids)

            current_app.logger.info(
                f"🔍 Spot {spot.id} - available: {is_available} (booked: {spot.id in booked_spot_ids}, leased: {str(spot.id) in leased_spot_ids}, pending: {spot.id in pending_spot_ids})")
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
        current_app.logger.error(f"❌ Error checking spot availability: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500



@booking_bp.route('/debug_redis', methods=['GET'])
def debug_redis():
    """Debug endpoint to check Redis connection and keys"""
    try:
        # Test Redis connection
        redis_ok = redis_client.ping()

        # Get all keys
        all_keys = redis_client.keys('*')

        # Get lease keys
        lease_keys = redis_client.keys('spot_lease:*')
        lease_data_keys = redis_client.keys('lease_data:*')

        # Get connection info
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


def process_direct_booking(data, sid):
    """Handle direct booking when Redis is down"""
    try:
        current_app.logger.info("🎯 Processing direct booking fallback")

        spot = ParkingSpot.query.get(data.get('spotId'))
        if not spot:
            emit('booking_failed', {'reason': 'Invalid spot'})
            return

        # Convert times
        start_time_str = f"{data.get('startHour')}:{data.get('startMinute')}"
        end_time_str = f"{data.get('endHour')}:{data.get('endMinute')}"

        start_time = datetime.strptime(start_time_str, "%H:%M").time()
        end_time = datetime.strptime(end_time_str, "%H:%M").time()
        booking_date = datetime.strptime(data.get('bookingDate'), '%Y-%m-%d').date()

        # 🎯 CRITICAL: Check for conflicts BEFORE creating pending booking
        with db.session.begin_nested():
            # 1. Check for conflicting CONFIRMED bookings
            conflict_count = Booking.query.filter(
                Booking.spot_id == int(data.get('spotId')),
                Booking.parking_lot_id == int(data.get('parkingLotId')),
                Booking.bookingDate == booking_date,
                Booking.startTime < end_time,
                Booking.endTime > start_time
            ).count()

            if conflict_count > 0:
                current_app.logger.error(f"❌ Spot {data.get('spotId')} already booked")
                emit('booking_failed', {'reason': 'This spot was just booked by someone else'})
                return

            # 2. Check for conflicting PENDING bookings
            conflicting_pending = PendingBooking.query.filter(
                PendingBooking.spot_id == int(data.get('spotId')),
                PendingBooking.parking_lot_id == int(data.get('parkingLotId')),
                PendingBooking.booking_date == booking_date,
                PendingBooking.start_time < end_time,
                PendingBooking.end_time > start_time
            ).first()

            if conflicting_pending:
                current_app.logger.warning(
                    f"❌ Spot {data.get('spotId')} has pending booking: {conflicting_pending.reservation_id}")
                emit('booking_failed',
                     {'reason': 'This spot is currently being booked by someone else. Please try again in a moment.'})
                return

        # 🎯 Only proceed if no conflicts found
        amount = calculate_price(start_time, end_time, spot.pricePerHour)
        reservation_id = str(uuid.uuid4())

        # 🎯 STORE AS PENDING BOOKING
        storage_success = store_pending_booking(
            reservation_id=reservation_id,
            user_id=current_user.get_id(),
            parking_lot_id=data.get('parkingLotId'),
            spot_id=data.get('spotId'),
            booking_date=booking_date,
            start_time=start_time,
            end_time=end_time,
            amount=amount
        )

        if not storage_success:
            emit('booking_failed', {'reason': 'Failed to process booking'})
            return

        # 🎯 CREATE STRIPE SESSION
        checkout_url = create_stripe_session_direct(
            data,
            start_time_str,
            end_time_str,
            spot,
            reservation_id
        )

        if not checkout_url:
            delete_pending_booking(reservation_id)
            emit('booking_failed', {'reason': 'Payment system error'})
            return

        emit('payment_redirect', {'url': checkout_url})

    except Exception as e:
        current_app.logger.error(f"❌ Direct booking error: {str(e)}")
        emit('booking_failed', {'reason': 'Booking failed. Please try again.'})



def create_stripe_session_direct(data, start_time_str, end_time_str, spot, reservation_id):
    """Create Stripe checkout session for direct booking (no Redis lease)"""
    try:
        # Calculate price
        start_time = datetime.strptime(start_time_str, "%H:%M").time()
        end_time = datetime.strptime(end_time_str, "%H:%M").time()
        hours = (end_time.hour - start_time.hour) + (end_time.minute - start_time.minute) / 60
        price = max(round(hours * spot.pricePerHour * 100), 50)

        # Create Stripe session
        success_url = f"{url_for('booking_bp.payment_success_direct', _external=True)}?session_id={{CHECKOUT_SESSION_ID}}"
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
                'user_id': str(current_user.get_id()),
                'direct_booking': 'true'  # Flag to indicate this is a direct booking
            }
        )

        return session.url

    except Exception as e:
        current_app.logger.error(f"Direct Stripe session creation failed: {str(e)}")
        return None


@booking_bp.route('/payment_success_direct', methods=['GET'])
def payment_success_direct():
    """Handle payment success for direct bookings (when Redis is down)"""
    session_id = request.args.get('session_id')
    current_app.logger.info(f"💰 Direct payment success called with session_id: {session_id}")

    try:
        session = stripe.checkout.Session.retrieve(session_id)

        if session.payment_status != 'paid':
            flash("Payment not completed. Please try again.", "error")
            return redirect(url_for('booking_bp.booking_form'))

        # Extract metadata
        reservation_id = session.metadata.get('reservation_id')
        spot_id = session.metadata.get('spot_id')
        user_id = session.metadata.get('user_id')
        parking_lot_id = session.metadata.get('parking_lot_id')
        booking_date = session.metadata.get('booking_date')
        start_time = session.metadata.get('start_time')
        end_time = session.metadata.get('end_time')

        # Convert times to proper objects
        start_time_obj = datetime.strptime(start_time, '%H:%M').time()
        end_time_obj = datetime.strptime(end_time, '%H:%M').time()
        booking_date_obj = datetime.strptime(booking_date, '%Y-%m-%d').date()

        # 🎯 CRITICAL: Use database transaction with locking to prevent race conditions
        with db.session.begin_nested():
            # 🎯 1. Check for conflicting CONFIRMED bookings first (most important)
            conflict_count = Booking.query.filter(
                Booking.spot_id == int(spot_id),
                Booking.parking_lot_id == int(parking_lot_id),
                Booking.bookingDate == booking_date_obj,
                Booking.startTime < end_time_obj,
                Booking.endTime > start_time_obj
            ).count()

            if conflict_count > 0:
                current_app.logger.error(f"❌ Spot {spot_id} already booked by someone else")
                delete_pending_booking(reservation_id)
                # Issue refund since spot is taken
                try:
                    refund = stripe.Refund.create(payment_intent=session.payment_intent)
                    current_app.logger.info(f"💸 Refund issued: {refund.id}")
                except Exception as refund_error:
                    current_app.logger.error(f"❌ Refund failed: {str(refund_error)}")

                flash("This spot was already booked by someone else. Refund issued.", "error")
                return redirect(url_for('booking_bp.booking_form'))

            # 🎯 2. Check for conflicting PENDING bookings from OTHER users
            # Use proper time overlap logic, not exact match
            conflicting_pending = PendingBooking.query.filter(
                PendingBooking.spot_id == int(spot_id),
                PendingBooking.parking_lot_id == int(parking_lot_id),
                PendingBooking.booking_date == booking_date_obj,
                PendingBooking.start_time < end_time_obj,
                PendingBooking.end_time > start_time_obj,
                PendingBooking.reservation_id != reservation_id  # Exclude current user's own pending booking
            ).first()

            if conflicting_pending:
                current_app.logger.warning(f"❌ Conflict with pending booking: {conflicting_pending.reservation_id}")
                delete_pending_booking(reservation_id)
                # Issue refund due to conflict
                try:
                    refund = stripe.Refund.create(payment_intent=session.payment_intent)
                    current_app.logger.info(f"💸 Refund issued: {refund.id}")
                except Exception as refund_error:
                    current_app.logger.error(f"❌ Refund failed: {str(refund_error)}")

                flash("This spot was reserved by someone else while you were paying. Refund issued.", "error")
                return redirect(url_for('booking_bp.booking_form'))

            # 🎯 3. Create the actual booking
            booking = Booking(
                userid=int(user_id),
                parking_lot_id=int(parking_lot_id),
                spot_id=int(spot_id),
                bookingDate=booking_date_obj,
                startTime=start_time_obj,
                endTime=end_time_obj,
                amount=float(session.amount_total) / 100  # Convert from cents
            )

            db.session.add(booking)
            db.session.flush()  # Get the booking ID without committing

            # Generate QR code
            generate_qr_code(booking.id)

            # 🎯 4. Clean up pending booking ONLY after successful booking
            delete_pending_booking(reservation_id)

        # Commit the transaction
        db.session.commit()

        current_app.logger.info(f"🎉 Direct booking completed successfully! Booking ID: {booking.id}")
        flash("Your booking and payment were successful!", "success")
        return redirect(url_for('dashboard.dashboard'))

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"❌ Direct payment success error: {str(e)}", exc_info=True)

        # Issue refund on error
        try:
            refund = stripe.Refund.create(payment_intent=session.payment_intent)
            current_app.logger.info(f"💸 Refund issued due to error: {refund.id}")
        except Exception as refund_error:
            current_app.logger.error(f"❌ Refund failed: {str(refund_error)}")

        flash("Payment received! If your booking doesn't appear, contact support for refund.", "warning")
        return redirect(url_for('dashboard.dashboard'))