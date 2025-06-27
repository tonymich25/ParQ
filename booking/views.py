import datetime
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required, current_user
from sqlalchemy import and_

from booking.forms import BookingForm
from config import City, db, ParkingLot, Booking, ParkingSpot

booking_bp = Blueprint('booking', __name__, template_folder='templates')


@booking_bp.route('/booking', methods=['GET', 'POST'])
@login_required
def book():
    form = BookingForm()
    cities = City.query.all()
    # *** FIX: Populate city choices before validation on both GET and POST requests ***
    # This ensures that when WTForms validates the submitted form, it knows what the valid options are.
    form.city.choices = [(city.id, city.city) for city in cities]

    if form.validate_on_submit():
        spot_id = request.form.get('spotId')
        if not spot_id:
            flash("You must select a parking spot.", "danger")
            return render_template('booking/booking.html', form=form)

        try:
            startTimeFormatted = datetime.datetime.strptime(form.startTime.data, "%H:%M").time()
            endTimeFormatted = datetime.datetime.strptime(form.endTime.data, "%H:%M").time()

            # Final check for availability before committing
            conflicting_bookings = Booking.query.filter(
                Booking.spot_id == spot_id,
                Booking.startTime < endTimeFormatted,
                Booking.endTime > startTimeFormatted
            ).count()

            if conflicting_bookings > 0:
                flash(
                    "Sorry, this spot was booked by someone else while you were deciding. Please select another time or spot.",
                    "warning")
                return render_template('booking/booking.html', form=form)

            newBooking = Booking(
                userid=current_user.get_id(),
                spot_id=spot_id,
                startTime=startTimeFormatted,
                endTime=endTimeFormatted,
            )

            db.session.add(newBooking)
            db.session.commit()

            flash("Your booking was successful!", "success")
            return redirect(url_for('dashboard.dashboard'))

        except Exception as e:
            db.session.rollback()
            flash(f"An error occurred: {e}", "danger")
            # It's good practice to log the error as well
            # current_app.logger.error(f"Booking error: {e}")

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
        'address': lot.address
    } for lot in parkingLots])


@booking_bp.route('/check_spot_availability', methods=['POST'])
def check_spot_availability():
    data = request.get_json()
    parking_lot_id = data.get('parkingLotId')
    start_time_str = data.get('startTime')
    end_time_str = data.get('endTime')

    if not all([parking_lot_id, start_time_str, end_time_str]):
        return jsonify({'error': 'Missing data'}), 400

    try:
        start_time = datetime.datetime.strptime(start_time_str, '%H:%M').time()
        end_time = datetime.datetime.strptime(end_time_str, '%H:%M').time()
    except ValueError:
        return jsonify({'error': 'Invalid time format'}), 400

    parking_lot = ParkingLot.query.get(parking_lot_id)
    if not parking_lot:
        return jsonify({'error': 'Parking lot not found'}), 404

    all_spots = parking_lot.spots

    conflicting_spot_ids = db.session.query(Booking.spot_id).filter(
        Booking.spot_id.in_([s.id for s in all_spots]),
        and_(
            Booking.startTime < end_time,
            Booking.endTime > start_time
        )
    ).distinct().all()

    booked_spot_ids = {item[0] for item in conflicting_spot_ids}

    spots_data = []
    for spot in all_spots:
        spots_data.append({
            'id': spot.id,
            'spot_number': spot.spot_number,
            'svg_coords': spot.svg_coords,
            'is_available': spot.id not in booked_spot_ids
        })

    return jsonify({
        'image_filename': parking_lot.image_filename,
        'spots': spots_data
    })
