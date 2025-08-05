from datetime import datetime, time, timedelta
import os
import threading
from collections import defaultdict
from zoneinfo import ZoneInfo

import qrcode
from flask_socketio import SocketIO
import stripe
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
            if is_spot_available(spot, session.metadata['parking_lot_id'],
                                 booking_date, start_time, end_time) > 0:
                # Spot taken → Refund
                stripe.Refund.create(payment_intent=session.payment_intent)
                emit_to_relevant_rooms_about_booking(spot, booking_date, True, False)

                if spot.id in spot_holds:
                    del spot_holds[spot.id]

                flash("Spot taken during payment. Refund issued.", "error")
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
        emit_to_relevant_rooms_about_booking(spot, booking_date, True, False)
        spot.heldBy = None
        spot.heldUntil = None
        current_app.logger.error(f"Unexpected error: {str(e)}")
        flash("Error processing your booking. Please contact support.", "error")
        return redirect(url_for('booking_bp.booking_form'))


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


def emit_to_relevant_rooms_about_booking(spot, bookingDate, isAvailable, return_confirmation):
    try:
        message = {
            'spotId': spot.id,
            'available': isAvailable,
        }

        # No need to parse date if it's already in correct format
        target_room = f"lot_{spot.parkingLotId}_{bookingDate}"
        print(f"Looking for room: {target_room}")
        print(f"Active rooms: {active_rooms}")

        if target_room in active_rooms:
            socketio.emit('spot_update', message, room=target_room)
            print(f"Emitted update for spot {spot.id} to room {target_room}")
            current_app.logger.debug(f"Emitted to: Spot {spot.id} availability={isAvailable} to {target_room}")
        else:
            print(f"Room {target_room} not found in active rooms")

        if return_confirmation is True:
            return True

        return None
    except Exception as e:
        print(f"Error in emit_to_relevant_rooms_about_booking: {str(e)}")
        current_app.logger.error(f"Error in emit_to_relevant_rooms_about_booking: {str(e)}")
        return False


@socketio.on('connect')
def handle_connect():
    print("Client connected: ", request.sid)
    active_connections[request.sid] = None


@socketio.on('disconnect')
def handle_disconnect():
    # TODO: ON DISCONNECT NEED TO EMIT BACK ANYTHING THAT USER WAS HOLDING0

    #ok = emit_to_relevant_rooms_about_booking(spotid, bookingDate, False, True)
    #print("Un-blocked spot on disconnect: ", ok)

    print("Client disconnected: " + request.sid)
    active_connections.pop(request.sid, None)





def calculate_price(startTime, endTime, spotPricePerHour):
    # Create datetime objects combining today's date with the times
    start_dt = datetime.combine(datetime.today().date(), startTime)
    end_dt = datetime.combine(datetime.today().date(), endTime)

    # Calculate duration in hours
    duration_hours = (end_dt - start_dt).total_seconds() / 3600

    # Calculate price in cents and ensure minimum charge
    price_cents = int(round(duration_hours * spotPricePerHour * 100))
    return max(price_cents, 50)  # Ensure minimum charge of 50 cents


def create_stripe_session(data, startTimeStr, endTimeStr, spot):
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
                'end_time': endTimeStr
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
            'held_until': hold_until,
            'user_id': current_user.get_id(),
            'parking_lot_id': data.get('parkingLotId'),
            'booking_date': data.get('bookingDate')
        }

        db.session.commit()

        ok = emit_to_relevant_rooms_about_booking(spot, data.get('bookingDate'), False, True)
        print("EMITTED ", ok)

        checkout_url = create_stripe_session(data, start_time_str, end_time_str, spot)
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
    now = datetime.now(ZoneInfo("Europe/Nicosia"))
    if spot.id in spot_holds:
        hold_data = spot_holds[spot.id]
        if hold_data['held_until'] > now:
            return 1  # Spot is held (not available)

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
def handle_join(data):
    parkingLotId = data.get('parkingLotId')
    bookingDate = data.get('bookingDate')

    if not parkingLotId or not bookingDate:
        return

    room_name = f"lot_{parkingLotId}_{bookingDate}"

    # Check if already in room
    rooms = socketio.server.rooms(request.sid)
    if room_name not in rooms:
        join_room(room_name)
        active_rooms[room_name].add(request.sid)
        print(f'User joined room: {room_name}')


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
        active_holds = {
            spot_id: hold
            for spot_id, hold in spot_holds.items()
            if (hold['held_until'] > now and
                hold.get('parking_lot_id') == parkingLotId and
                hold.get('booking_date') == bookingDate)
        }

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