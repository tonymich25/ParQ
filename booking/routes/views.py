from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required
from booking.forms import BookingForm
from config import app, db, City, ParkingLot, Booking, ParkingSpot, socketio, redis_client, PendingBooking, ActiveConnection

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


