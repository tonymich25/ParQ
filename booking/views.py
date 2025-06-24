from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required

from booking.forms import BookingForm
from config import City, db, ParkingLot

booking_bp = Blueprint('booking', __name__, template_folder='templates')

@login_required
@booking_bp.route('/booking', methods=['GET', 'POST'])
def book():

        form = BookingForm()


        cities = City.query.all()

        form.city.choices = [(city.id, city.city) for city in cities]
        form.parkingLot = []

        return render_template('booking/booking.html', form=form)

@booking_bp.route('/city_selected', methods=['POST'])
def city_selected():
    data = request.get_json()
    cityName = data.get('city')
    city = City.query.filter_by(city=cityName).first()
    parkingLots = ParkingLot.query.filter_by(city_id=city.id).all()
    return jsonify([{
        'id': lot.id,
        'name': lot.address,  # Using address as the display name
        'address': lot.address  # Also include address for geocoding
    } for lot in parkingLots])
