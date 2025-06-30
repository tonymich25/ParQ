from datetime import date
from flask import render_template, Blueprint, send_from_directory, current_app
from flask_login import login_required, current_user

from config import Booking

dashboard_bp = Blueprint('dashboard', __name__, template_folder='dashboard')


@dashboard_bp.route('/dashboard', methods=['GET', 'POST'])
@login_required
def dashboard():

    bookings = Booking.query.filter(Booking.userid == current_user.id, Booking.bookingDate >= date.today()).all()
    nextBooking = Booking.query.filter(Booking.userid==current_user.id, Booking.bookingDate >= date.today()).order_by(Booking.bookingDate.asc()).first()
    history = Booking.query.filter(Booking.userid==current_user.id, Booking.bookingDate < date.today()).order_by(Booking.bookingDate.desc()).limit(5).all()

    print(history)
    return render_template('dashboard/dashboard.html', current_user=current_user, bookings=bookings, nextBooking=nextBooking, history=history)


@dashboard_bp.route('/qr/<int:booking_id>')
@login_required
def show_qr(booking_id):
    return send_from_directory('static/qr_codes', f'{booking_id}.png')