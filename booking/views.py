from flask import Blueprint, render_template, request, jsonify, redirect, url_for
from flask_login import login_required, current_user

from booking.forms import BookingForm
from config import City, db, ParkingLot, Booking

booking_bp = Blueprint('booking', __name__, template_folder='templates')


@booking_bp.route('/booking', methods=['GET', 'POST'])
@login_required
def book():
    form = BookingForm()
    cities = City.query.all()
    form.city.choices = [(city.id, city.city) for city in cities]


    print("\n=== FORM DATA ===")
    print(f"City data: {form.city.data}")
    print(f"ParkingLot data: {form.parkingLot.data}")
    print(f"Errors: {form.errors}")

    if form.validate_on_submit():
        print("Form validated successfully!")
        parking_lot = ParkingLot.query.filter_by(id=form.parkingLot.data)

        if parking_lot:

            newBooking = Booking(
                userid = current_user.get_id(),
                city = form.city.data,
                parkinglot = form.parkingLot.data
            )

            db.session.add(newBooking)
            db.session.commit()


        # USE THIS!
        if not parking_lot:  # Manual check
            #flash("Invalid parking lot selected", "error")
            return redirect(url_for('booking.book'))


        # Proceed with booking


        return redirect(url_for('dashboard.dashboard'))

    return render_template('booking/booking.html', form=form)

@booking_bp.route('/city_selected', methods=['POST'])
def city_selected():
    data = request.get_json()
    city_id = data.get('city')
    parkingLots = ParkingLot.query.filter_by(city_id=city_id).all()
    return jsonify([{
        'id': lot.id,
        'name': lot.address,
        'address': lot.address
    } for lot in parkingLots])
