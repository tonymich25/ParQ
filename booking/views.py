from flask import Blueprint, render_template, request, jsonify

from booking.forms import BookingForm
from config import City, db

booking_bp = Blueprint('booking', __name__, template_folder='templates')


@booking_bp.route('/booking', methods=['GET', 'POST'])
def book():

        form = BookingForm()


        cities = City.query.all()

        form.city.choices = [(city.id, city.city) for city in cities]

        return render_template('booking/booking.html', form=form)

@booking_bp.route('/city_selected', methods=['POST'])
def city_selected():
    data = request.get_json()
    city = data.get('city')
    print("City selected:", city)
    return jsonify({'status': 'ok'})

