import os
import threading
import qrcode
import stripe
from collections import defaultdict
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta
from flask_socketio import leave_room
from cryptography.fernet import Fernet
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from flask_socketio import join_room, emit

from booking.forms import BookingForm
from config import City, db, ParkingLot, Booking, ParkingSpot, app, socketio

booking_bp = Blueprint('booking_bp', __name__, template_folder='templates')

active_rooms = defaultdict(set)  # Format: {"lot_1_2023-12-25": set("socket_id1", "socket_id2")}
active_connections = {}
room_in_payment = defaultdict(set)
spot_holds = {}

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




def create_booking(session, spot):
    return Booking(
        userid=session.metadata.get('user_id'),
        parking_lot_id=spot.parkingLotId,
        spot_id=session.metadata.get('spot_id'),
        bookingDate=datetime.strptime(session.metadata.get('booking_date'), '%Y-%m-%d').date(),
        startTime=datetime.strptime(session.metadata.get('start_time'), '%H:%M').time(),
        endTime=datetime.strptime(session.metadata.get('end_time'), '%H:%M').time(),
        amount=float(session.amount_total) / 100,
    )



@booking_bp.route('/payment_success', methods=['GET'])
def payment_success():

    session_id = request.args.get('session_id')

    if not session_id:
        flash("Invalid payment session. Please try again.", "error")
        return redirect(url_for('booking_bp.booking_form'))

    try:
        session = stripe.checkout.Session.retrieve(session_id)

        booking_date = session.metadata.get('booking_date')
        start_time = session.metadata.get('start_time')
        end_time = session.metadata.get('end_time')

        session.metadata.get('user_id')


        spot = ParkingSpot.query.get(session.metadata.get('spot_id'))

        #if session.payment_status != 'paid':
            #flash("Payment not completed. Please try again.", "error")
            #emit_to_relevant_rooms_about_booking(spot, session.metadata.get('booking_date'), True)
            #spot.heldUntil = None
            #spot.heldBy = None
            #db.session.commit()
            #return redirect(url_for('booking_bp.book'))



        with db.session.begin_nested():

            if spot.id not in spot_holds or spot_holds[spot.id]['user_id'] != current_user.id:
                flash("Booking session expired. Please try again.", "error")
                return redirect(url_for('booking_bp.booking_form'))

            start_time_obj = datetime.strptime(start_time, '%H:%M').time()
            end_time_obj = datetime.strptime(end_time, '%H:%M').time()

            if is_spot_available(spot, session.metadata['parking_lot_id'],
                                 booking_date, start_time_obj, end_time_obj) > 0:
                # Spot taken -> Refund
                stripe.Refund.create(payment_intent=session.payment_intent)
                emit_to_relevant_rooms_about_booking(spot, booking_date, True, False)

                if spot.id in spot_holds:
                    del spot_holds[spot.id]

                flash("Spot taken during payment. Refund issued.", "error")
                return redirect(url_for('booking_bp.booking_form'))

            hold_until = datetime.fromtimestamp(float(session.metadata['hold_until']),tz=ZoneInfo("Europe/Nicosia"))
            if hold_until <= datetime.now(ZoneInfo("Europe/Nicosia")):
                stripe.Refund.create(payment_intent=session.payment_intent)
                emit_to_relevant_rooms_about_booking(spot, booking_date, True, False)

                if spot.id in spot_holds:
                    del spot_holds[spot.id]

                flash("Spot holding time passed. Refund issued.", "error")
                return redirect(url_for('booking_bp.booking_form'))


        if spot.id in spot_holds:
            del spot_holds[spot.id]

        new_booking = create_booking(session, spot)

        db.session.add(new_booking)
        db.session.commit()


        generate_qr_code(new_booking.id)

        disconnect_user(session)


        flash("Your booking and payment were successful!", "success")


        return redirect(url_for('dashboard.dashboard'))

    except stripe.error.StripeError as e:
        emit_to_relevant_rooms_about_booking(spot, booking_date, True, False)
        spot.heldBy = None
        spot.heldUntil = None
        current_app.logger.error(f"Stripe error: {str(e)}")
        flash("Payment processing error. Please contact support.", "error")
        return redirect(url_for('booking_bp.booking_form'))
    except Exception as e:
            # Safely release resources without assuming variables exist
        spot_id = session.metadata.get('spot_id') if session else None
        booking_date = session.metadata.get('booking_date') if session else None

        if spot_id:
            spot = ParkingSpot.query.get(spot_id)
        if spot:  # Only emit if spot exists
            emit_to_relevant_rooms_about_booking(spot, booking_date, True, False)
        if spot_id in spot_holds:  # Cleanup in-memory hold
            del spot_holds[spot_id]

        current_app.logger.error(f"Unexpected error: {str(e)}", exc_info=True)
        flash("Payment received! If your booking doesn’t appear, contact support.", "warning")
        return redirect(url_for('dashboard.dashboard'))  # Avoid booking loop

def generate_qr_code(new_booking_id):
    key = os.getenv("FERNET_KEY")
    cipher = Fernet(key.encode())
    encrypted = cipher.encrypt(str(new_booking_id).encode()).decode()

    img = qrcode.make(encrypted)
    img.save(f"static/qr_codes/{new_booking_id}.png")



def disconnect_user(session):
    user_id = session.metadata['user_id']
    for room_name, sids in active_rooms.items():
        if user_id in {s.split('_')[0] for s in sids}:  # Assuming sid contains user_id
            for sid in list(sids):
                if sid.startswith(f"{user_id}_"):
                    emit('payment_complete', {}, room=sid)
                    socketio.disconnect(sid)  # Close connection
                    sids.remove(sid)


def emit_to_relevant_rooms_about_booking(spot, booking_date, is_available, return_confirmation, start_time=None, end_time=None):
    try:
        target_room = f"lot_{spot.parkingLotId}_{booking_date}"
        print(f"\n=== Starting emission to {target_room} ===")
        print(f"Spot: {spot.id} | Available: {is_available} | Time Range: {start_time}-{end_time}")

        if target_room not in active_rooms:
            print(f"Room {target_room} not found")
            return False if return_confirmation else None

        # Convert input times
        if isinstance(start_time, str) and start_time:
            start_time = datetime.strptime(start_time, "%H:%M").time()
        if isinstance(end_time, str) and end_time:
            end_time = datetime.strptime(end_time, "%H:%M").time()

        recipients = 0
        for sid in list(active_rooms[target_room]):
            conn_data = active_connections.get(sid)
            if not conn_data:
                print(f"Missing connection data for {sid}")
                continue

            # Get client's time range with validation
            try:
                conn_start = datetime.strptime(conn_data['startTime'], "%H:%M").time() if conn_data['startTime'] else None
                conn_end = datetime.strptime(conn_data['endTime'], "%H:%M").time() if conn_data['endTime'] else None
            except ValueError as e:
                print(f"Invalid time format for {sid}: {e}")
                continue

            # Determine if we should send the update
            send_update = True
            if start_time and end_time and conn_start and conn_end:
                # Check for time overlap
                time_overlap = not (conn_end <= start_time or conn_start >= end_time)
                send_update = time_overlap
                print(f"Client {sid} | Times: {conn_data['startTime']}-{conn_data['endTime']} | Overlap: {time_overlap}")

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
    active_connections[request.sid] = None


@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    print(f"\nClient disconnecting: {sid}")

    # Clean up from all rooms
    for room_name in list(socketio.server.rooms(sid)):
        if room_name.startswith('lot_'):
            if room_name in active_rooms:
                active_rooms[room_name].discard(sid)
                if not active_rooms[room_name]:
                    del active_rooms[room_name]
            print(f"Removed from room: {room_name}")

    # Clean up connection data
    if sid in active_connections:
        del active_connections[sid]

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


def create_stripe_session(data, startTimeStr, endTimeStr, spot, hold_until):
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
                'user_id': current_user.get_id(),
                'spot_id': data.get('spotId'),
                'parking_lot_id': data.get('parkingLotId'),
                'booking_date': data.get('bookingDate'),
                'start_time': startTimeStr,
                'end_time': endTimeStr,
                'hold_until': hold_until
            }
        )
        return checkout_session.url
    except Exception as e:
        current_app.logger.error(f"Stripe session creation failed: {str(e)}", exc_info=True)
        return None


@socketio.on('book_spot')
def book_spot(data):
    ok = False
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

        if is_spot_available(spot, data.get('parkingLotId'), data.get('bookingDate'), start_time, end_time) > 0:
            emit('booking_failed', {'reason': 'taken'})
            return

        hold_until = datetime.now(ZoneInfo("Europe/Nicosia")) + timedelta(seconds=240)
        spot_holds[spot.id] = {
            'held_until': hold_until.timestamp(),
            'user_id': current_user.get_id(),
            'parking_lot_id': data.get('parkingLotId'),
            'booking_date': data.get('bookingDate'),
            'start_time': start_time_str,
            'end_time': end_time_str
        }

        db.session.commit()

        ok = emit_to_relevant_rooms_about_booking(spot, data.get('bookingDate'), False, True, start_time, end_time)
        print("EMITTED ", ok)

        checkout_url = create_stripe_session(data, start_time_str, end_time_str, spot, hold_until)
        if not checkout_url:
            emit('booking_failed', {'reason': 'Payment system error'})
            emit_to_relevant_rooms_about_booking(spot, data.get('bookingDate'), True, False)
            del spot_holds[spot.id]  # Remove hold if payment fails
            db.session.flush()

            return

        threading.Timer(240, lambda: release_spot_if_unpaid(spot.id, data.get('bookingDate'))).start()

        emit('payment_redirect', {
            'url': checkout_url,
        })

        room_in_payment[checkout_url].add(request.sid)
        print("added to payment room")

    except Exception as e:

        if ok is True:
            emit_to_relevant_rooms_about_booking(data.get('spotId'), data.get('bookingDate'), True, False)
            spot.heldBy = None
            spot.heldUntil = None


        emit('booking_failed', {
            'reason': str(e)
        })



def is_spot_available(spot, parkingLotId, bookingDate, startTime, endTime):
    if spot.id in spot_holds:
        hold_data = spot_holds[spot.id]

        held_until = datetime.fromtimestamp(float(hold_data['held_until']), tz=ZoneInfo("Europe/Nicosia"))

        if held_until > datetime.now(ZoneInfo("Europe/Nicosia")):
            held_start = datetime.strptime(hold_data['start_time'], "%H:%M").time()
            held_end = datetime.strptime(hold_data['end_time'], "%H:%M").time()
            # If current user overlaps , consider spot available
            if hold_data['booking_date'] == bookingDate and not (endTime <= held_start or startTime >= held_end):

            # If the dates are the same and times overlap, spot is not available
                if hold_data['user_id'] == current_user.id:
                    return 0  # Spot is held (not available) for overlapping time
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
    with app.app_context():
        try:
            print(f"Checking if spot {spot_id} should be released")
            now = datetime.now(ZoneInfo("Europe/Nicosia"))

            if spot_id in spot_holds:
                hold_data = spot_holds[spot_id]
                if hold_data['held_until'] < now:
                    print(f"Releasing hold on spot {spot_id}")
                    spot = ParkingSpot.query.get(spot_id)
                    if spot:
                        emit_to_relevant_rooms_about_booking(spot, bookingDate, True, False)
                    del spot_holds[spot_id]
                else:
                    print(f"Hold on spot {spot_id} not yet expired")
            else:
                print(f"No active hold found for spot {spot_id}")

        except Exception as e:
            print(f"Error in release_spot_if_unpaid: {str(e)}")
            current_app.logger.error(f"Error releasing spot {spot_id}: {str(e)}")


#@socketio.on('get_spots_availability')
#def get_spots_availability(data):


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

        current_lot_rooms = [room for room in socketio.server.rooms(request.sid) if room.startswith('lot_')]

        if new_room_name in current_lot_rooms:
            # Same room: just update time range
            active_connections[request.sid].update({
                'startTime': start_time,
                'endTime': end_time
            })
            print(f"Client {request.sid} updated time range in existing room {new_room_name} ({start_time}-{end_time})")
        else:
            # Leave old lot rooms
            for room in current_lot_rooms:
                leave_room(room)
                active_rooms[room].discard(request.sid)
                if not active_rooms[room]:
                    del active_rooms[room]

            # Join new room
            join_room(new_room_name)
            active_rooms.setdefault(new_room_name, set()).add(request.sid)

            # Update connection info without lastActive
            active_connections[request.sid] = {
                'parkingLotId': parking_lot_id,
                'bookingDate': booking_date,
                'startTime': start_time,
                'endTime': end_time
            }
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
        # Get all active holds for this parking lot and date
        active_holds = {}
        for spot_id, hold in spot_holds.items():
            held_until = hold['held_until']
            if isinstance(held_until, float):
                held_until = datetime.fromtimestamp(held_until, ZoneInfo("Europe/Nicosia"))
            if held_until > now and hold.get('parking_lot_id') == parkingLotId:
                # Check if the hold is for the same date and times overlap
                held_start = datetime.strptime(hold['start_time'], "%H:%M").time()
                held_end = datetime.strptime(hold['end_time'], "%H:%M").time()
                if hold['booking_date'] == bookingDate and not (endTime <= held_start or startTime >= held_end):
                    active_holds[spot_id] = hold

        # Get conflicting bookings
        conflicting_bookings = Booking.query.filter(
            Booking.parking_lot_id == parkingLotId,
            Booking.bookingDate == bookingDate,
            Booking.startTime < endTime,
            Booking.endTime > startTime
        ).with_entities(Booking.spot_id).all()

        booked_spot_ids = {b[0] for b in conflicting_bookings}
        held_spot_ids = set(active_holds.keys())

        spots_data = []
        for spot in allSpots:
            is_available = (spot.id not in booked_spot_ids and
                            spot.id not in held_spot_ids)

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
            'holds': list(held_spot_ids)  # For debugging
        })

    except Exception as e:
        current_app.logger.error(f"Error checking spot availability: {str(e)}")
        return jsonify({'error': str(e)}), 500
