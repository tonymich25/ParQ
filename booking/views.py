import datetime
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required, current_user
from booking.forms import BookingForm
from config import City, db, ParkingLot, Booking, ParkingSpot

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

        # Final check for availability before committing
        conflicting_bookings = Booking.query.filter(
        Booking.spot_id == spotId,
        Booking.startTime < endTimeFormatted,
            Booking.endTime > startTimeFormatted
        ).count()

        if conflicting_bookings > 0:
            flash("Sorry, this spot was booked by someone else while you were deciding. Please select another time or spot.","warning")
            return render_template('booking/booking.html', form=form)

        newBooking = Booking(
            userid=current_user.get_id(),
            spot_id=spotId,
            bookingDate=form.bookingDate.data,
            startTime=startTimeFormatted,
            endTime=endTimeFormatted,
        )

        db.session.add(newBooking)
        db.session.commit()

        flash("Your booking was successful!", "success")
        return redirect(url_for('dashboard.dashboard'))



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
    parkingLotId = data.get('parkingLotId')
    startTime_str = data.get('startTime')
    endTime_str = data.get('endTime')


    startTime = datetime.datetime.strptime(startTime_str, '%H:%M').time()
    endTime = datetime.datetime.strptime(endTime_str, '%H:%M').time()


    parkingLot = ParkingLot.query.get(parkingLotId)
    allSpots = parkingLot.spots
    spotIds = [s.id for s in allSpots]

    conflicting_spot_ids =  Booking.query.filter(
            Booking.spot_id.in_(spotIds),
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
