from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from flask import current_app
from config import PendingBooking, db


def store_pending_booking(reservation_id, user_id, parking_lot_id, spot_id,
                          booking_date, start_time, end_time, amount):
    """Store booking in pending_bookings table"""
    try:
        pending_booking = PendingBooking(
            reservation_id=reservation_id,
            user_id=user_id,
            parking_lot_id=parking_lot_id,
            spot_id=spot_id,
            booking_date=booking_date,
            start_time=start_time,
            end_time=end_time,
            amount=amount,
            expires_at=datetime.now(ZoneInfo("Europe/Nicosia")) + timedelta(minutes=4)
        )
        db.session.add(pending_booking)
        db.session.commit()
        return True
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Failed to store pending booking: {str(e)}")
        return False


def delete_pending_booking(reservation_id):
    """Delete from pending_bookings table"""
    try:
        PendingBooking.query.filter_by(reservation_id=reservation_id).delete()
        db.session.commit()
        return True
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Failed to delete pending booking: {str(e)}")
        return False


# def get_pending_booking(reservation_id):
#     """Retrieve pending booking data from database"""
#     try:
#         # Clean up expired bookings first
#         expired_count = PendingBooking.query.filter(PendingBooking.expires_at < datetime.now()).delete()
#         if expired_count > 0:
#             current_app.logger.info(f"Cleaned up {expired_count} expired pending bookings")
#         db.session.commit()
#
#         pending_booking = PendingBooking.query.filter_by(reservation_id=reservation_id).first()
#         if pending_booking:
#             current_app.logger.info(f"Retrieved pending booking {reservation_id}")
#             return {
#                 'user_id': pending_booking.user_id,
#                 'parking_lot_id': pending_booking.parking_lot_id,
#                 'spot_id': pending_booking.spot_id,
#                 'booking_date': pending_booking.booking_date,
#                 'start_time': pending_booking.start_time,
#                 'end_time': pending_booking.end_time,
#                 'amount': pending_booking.amount
#             }
#         current_app.logger.warning(f"Pending booking not found: {reservation_id}")
#         return None
#
#     except Exception as e:
#         current_app.logger.error(f"Failed to retrieve pending booking: {str(e)}")
#         return None