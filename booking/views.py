import datetime
from cryptography.fernet import Fernet
import qrcode
import os
import stripe
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from booking.forms import BookingForm
from config import City, db, ParkingLot, Booking, ParkingSpot
import qrcode

import base64

booking_bp = Blueprint('booking', __name__, template_folder='templates')


@booking_bp.route('/booking', methods=['GET', 'POST'])
@login_required
def book():
    form = BookingForm()
    cities = City.query.all()

    form.city.choices = [(city.id, city.city) for city in cities]

    if form.validate_on_submit():


        spotId = request.form.get('spotId')
        startTimeFormatted = datetime.datetime.strptime(form.startTime.data, "%H:%M").time()
        endTimeFormatted = datetime.datetime.strptime(form.endTime.data, "%H:%M").time()
        spot = ParkingSpot.query.get(spotId)



        # Final check for availability before committing
        conflicting_bookings = Booking.query.filter(
        Booking.spot_id == spotId,
        Booking.bookingDate == form.bookingDate.data,
        Booking.startTime < endTimeFormatted,
        Booking.endTime > startTimeFormatted).count()

        hours = endTimeFormatted.hour - startTimeFormatted.hour
        price = hours * spot.pricePerHour

        if conflicting_bookings > 0:
            flash("Sorry, this spot was booked by someone else while you were deciding. Please select another time or spot.","warning")
            return render_template('booking/booking.html', form=form)

        try:
            checkout_session = stripe.checkout.Session.create(
                payment_method_types=['card'],
                line_items=[{
                    'price_data': {
                        'currency': 'eur',  # or your currency
                        'product_data': {
                            'name': f'Parking Spot #{spot.spotNumber}',
                        },
                        'unit_amount': int(price * 100),  # Stripe uses cents
                    },
                    'quantity': 1,
                }],
                mode='payment',
                success_url=url_for('booking.payment_success', _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
                cancel_url=url_for('booking.book', _external=True),
                metadata={
                    'user_id': current_user.get_id(),
                    'spot_id': spotId,
                    'booking_date': form.bookingDate.data.strftime('%Y-%m-%d'),
                    'start_time': form.startTime.data,
                    'end_time': form.endTime.data
                }
            )

            # Store the session ID temporarily (you might want to store it in the database)
            return redirect(checkout_session.url, code=303)

        except Exception as e:
            flash("Payment processing error. Please try again.", "error")
            current_app.logger.error(f"Stripe error: {str(e)}")
            return render_template('booking/booking.html', form=form)



    # This part runs for GET requests or if form validation fails
    return render_template('booking/booking.html', form=form)


@booking_bp.route('/city_selected', methods=['POST'])
def city_selected():
    data = request.get_json()
    city_id = data.get('city')
    parkingLots = ParkingLot.query.filter_by(city_id=city_id).all()

    return jsonify([{
        'id': lot.id,
        'name': lot.name,
        'address': lot.address} for lot in parkingLots])


@booking_bp.route('/check_spot_availability', methods=['POST'])
def check_spot_availability():
    data = request.get_json()
    parkingLotId = data.get('parkingLotId')
    startTime_str = data.get('startTime')
    endTime_str = data.get('endTime')

    bookingDate = data.get('bookingDate')
    startTime = datetime.datetime.strptime(startTime_str, '%H:%M').time()
    endTime = datetime.datetime.strptime(endTime_str, '%H:%M').time()


    parkingLot = ParkingLot.query.get(parkingLotId)
    allSpots = parkingLot.spots
    spotIds = [s.id for s in allSpots]

    conflicting_spot_ids =  Booking.query.filter(
        Booking.spot_id.in_(spotIds),
        Booking.bookingDate == bookingDate,
        Booking.startTime < endTime,
        Booking.endTime > startTime).distinct().with_entities(Booking.spot_id).all()


    bookedSpotIds = {item[0] for item in conflicting_spot_ids}

    spots_data = []
    for spot in allSpots:
        spots_data.append({
            'id': spot.id,
            'spotNumber': spot.spotNumber,
            'svgCoords': spot.svgCoords,
            'is_available': spot.id not in bookedSpotIds
        })

    return jsonify({
        'image_filename': parkingLot.image_filename,
        'spots': spots_data
    })


@booking_bp.route('/payment_success', methods=['GET'])
def payment_success():
    session_id = request.args.get('session_id')

    if not session_id:
        flash("Invalid payment session. Please try again.", "error")
        return redirect(url_for('booking.book'))

    try:
        # Retrieve the session to verify payment
        session = stripe.checkout.Session.retrieve(session_id)

        if session.payment_status != 'paid':
            flash("Payment not completed. Please try again.", "error")
            return redirect(url_for('booking.book'))

        # Create the booking record
        new_booking = Booking(
            userid=session.metadata.get('user_id'),
            spot_id=session.metadata.get('spot_id'),
            bookingDate=datetime.datetime.strptime(session.metadata.get('booking_date'), '%Y-%m-%d').date(),
            startTime=datetime.datetime.strptime(session.metadata.get('start_time'), '%H:%M').time(),
            endTime=datetime.datetime.strptime(session.metadata.get('end_time'), '%H:%M').time(),
            amount=float(session.amount_total) / 100,
        )

        db.session.add(new_booking)
        db.session.commit()

        key = os.getenv("FERNET_KEY")
        cipher = Fernet(key.encode())
        encrypted = cipher.encrypt(str(new_booking.id).encode()).decode()

        img = qrcode.make(encrypted)
        img.save(f"qrcodes/{new_booking.id}.png")


        flash("Your booking and payment were successful!", "success")
        return redirect(url_for('dashboard.dashboard'))

    except stripe.error.StripeError as e:
        current_app.logger.error(f"Stripe error: {str(e)}")
        flash("Payment processing error. Please contact support.", "error")
        return redirect(url_for('booking.book'))
    except Exception as e:
        current_app.logger.error(f"Unexpected error: {str(e)}")
        flash("Error processing your booking. Please contact support.", "error")
        return redirect(url_for('booking.book'))
