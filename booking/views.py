from datetime import datetime
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
            bookingDate=datetime.datetime.strptime(session.metadata.get('booking_date'), '%Y-%m-%d').date(),
            startTime=datetime.datetime.strptime(session.metadata.get('start_time'), '%H:%M').time(),
            endTime=datetime.datetime.strptime(session.metadata.get('end_time'), '%H:%M').time(),
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
                emit_to_relevant_rooms_about_booking(spot, booking_date, True, None)

                spot.heldBy = None
                spot.heldUntil = None

                flash("Spot taken during payment. Refund issued.", "error")
                return redirect(url_for('booking_bp.booking_form'))


        spot.heldUntil = None

        new_booking = create_booking(session, spot)


        db.session.add(new_booking)
        db.session.commit()


        generate_qr_code(new_booking.id)

        disconnect_user(session)


        flash("Your booking and payment were successful!", "success")


        return redirect(url_for('dashboard.dashboard'))

    except stripe.error.StripeError as e:
        emit_to_relevant_rooms_about_booking(spot, booking_date, True, None)
        spot.heldBy = None
        spot.heldUntil = None
        current_app.logger.error(f"Stripe error: {str(e)}")
        flash("Payment processing error. Please contact support.", "error")
        return redirect(url_for('booking_bp.booking_form'))
    except Exception as e:
        emit_to_relevant_rooms_about_booking(spot, booking_date, True, None)
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

    message = {
        'spotId': spot.id,
        'available': isAvailable,
    }

    date_str = bookingDate.strftime('%Y-%m-%d')
    target_room_prefix = f"lot_{spot.parkingLotId}_{date_str}"

    for room_name in active_rooms.keys():
        if room_name == target_room_prefix:
            emit('spot_update', message, room=room_name)
            current_app.logger.debug(f"Spot {spot.id} availability={isAvailable} to {room_name}")

    if return_confirmation is True:
        return True

    return None


@socketio.on('connect')
def handle_connect():
    print("Client connected: " + request.sid)
    active_connections[request.sid] = None


@socketio.on('disconnect')
def handle_connect():
    print("Client disconnected: " + request.sid)
    active_connections.pop[request.sid] = None


def calculate_price(startTime, endTime, spotPricePerHour):
    duration_hours = (endTime - startTime).total_seconds() / 3600
    price_cents = int(round(duration_hours * spotPricePerHour * 100))
    return price_cents


def create_stripe_session(data, startTime, endTime, spot):

    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'eur',
                    'product_data': {
                        'name': f'Parking Spot #{spot.spotNumber}',
                    },
                    'unit_amount': calculate_price(startTime, endTime, spot.pricePerHour),
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
                'start_time': startTime,
                'end_time': endTime
            }
        )

        return checkout_session.url

    except Exception as e:
        current_app.logger.error(f"Stripe session creation failed: {str(e)}")
        return None


@socketio.on('book_spot')
def book_spot(data):
    try:
        with db.session.begin_nested():

            spot = ParkingSpot.query.get(data.get('spotId'))

            if not spot:
                emit('booking_failed', {'reason': 'Invalid spot'})
                return

            startTime = datetime.time(data.get('startHour'), data.get('startMinute'))
            endTime = datetime.time(data.get('endHour'), data.get('endMinute'))

            if is_spot_available(spot, data.get('parkingLotId'), data.get('bookingDate'), startTime, endTime) > 0:
                emit('booking_failed', {'reason': 'taken'})
                return



            spot.held_until = datetime.now(ZoneInfo("Europe/Nicosia")) + datetime.timedelta(minutes=3)
            spot.held_by = data[current_user.get_id()]
            db.session.flush()

            ok = emit_to_relevant_rooms_about_booking(spot, data.get('bookingDate'), False, True)


        checkout_url = create_stripe_session(data, startTime, endTime, spot)
        if not checkout_url:
            emit('booking_failed', {'reason': 'Payment system error'})
            emit_to_relevant_rooms_about_booking(spot, data.get('bookingDate'), True, None)
            spot.heldUntil = None
            spot.heldBy = None
            db.session.commit()

            return

        threading.Timer(300, release_spot_if_unpaid(spot, data.get('bookingDate')), args=[spot.id]).start()


        emit('payment_redirect', {
            'url': checkout_url,
        })



    except Exception as e:

        if ok is True:
            emit_to_relevant_rooms_about_booking(spot, data.get('bookingDate'), True, None)
            spot.heldBy = None
            spot.heldUntil = None


        emit('booking_failed', {
            'reason': str(e)
        })



def is_spot_available(spot, parkingLotId, bookingDate, startTime, endTime):

    if spot.held_until and spot.held_until > datetime.now(ZoneInfo("Europe/Nicosia")):
        return 1  # Spot is held (not available)

        # Check existing bookings
    return Booking.query.filter(
        Booking.spot_id == spot.id,
        Booking.parking_lot_id == parkingLotId,
        Booking.bookingDate == bookingDate,
        Booking.startTime < endTime,
        Booking.endTime > startTime
    ).count()


def release_spot_if_unpaid(spot, bookingDate):
    with app.app_context():
        if spot.held_until and spot.held_until < datetime.now(ZoneInfo("Europe/Nicosia")):
            spot.held_until = None
            spot.held_by = None
            db.session.commit()
            emit_to_relevant_rooms_about_booking(spot, bookingDate, True, None)


@socketio.on('subscribe')
def handle_join(data):
    parkingLotId = data.get('parkingLotId')
    bookingDate = data.get('bookingDate')
    startTime = data.get('endTime')
    endTime = data.get('endTime')

    room_name = f"lot_{data['parkingLotId']}_{data['bookingDate']}"
    join_room(room_name)
    print('User joined room: ' + room_name)

    parkingLot = ParkingLot.query.get(parkingLotId)
    allSpots = parkingLot.spots
    spotIds = [s.id for s in allSpots]

    conflicting_spot_ids = Booking.query.filter(
        Booking.spot_id.in_(spotIds),
        Booking.bookingDate == bookingDate,
        Booking.startTime < endTime,
        Booking.endTime > startTime).distinct().with_entities(Booking.spot_id).all()

    bookedSpotIds = {item[0] for item in conflicting_spot_ids}

    spots_data = []
    for spot in allSpots:
        spots_data.append({
            'spotId': spot.id,
            'is_available': spot.id not in bookedSpotIds,
        })

    emit('batch_update', {
        'type': 'batch_update',
        'spots': spots_data
    })
