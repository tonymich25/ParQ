from flask import Blueprint


booking_bp = Blueprint('booking', __name__, template_folder='templates')

@booking_bp.route('/booking', methods=['GET', 'POST'])
def book():


@booking_bp.route('/city_selected', methods=['POST'])
def city_selected():

