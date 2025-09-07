# your_availability_logic.py
from datetime import datetime, time
from zoneinfo import ZoneInfo

from config import db
from sqlalchemy import text

def is_spot_available_in_db(user_id, spot_id, booking_data):
    """
    The ultimate fallback: check the source of truth (PostgreSQL).
    Reuses the same logic from your REST endpoint.
    """
    parking_lot_id = booking_data['parking_lot_id']
    booking_date = booking_data['booking_date']
    start_time = booking_data['start_time']
    end_time = booking_data['end_time']
    #requested_start_time = datetime.strptime(start_time, '%H:%M').time()
    #requested_end_time = datetime.strptime(end_time, '%H:%M').time()

    # This is a direct SQL version of your existing availability check
    query = text("""
        SELECT COUNT(*) FROM pending_bookings 
        WHERE user_id = :user_id 
        AND spot_id = :spot_id
        AND parking_lot_id = :parking_lot_id
        AND booking_date = :booking_date
        AND start_time < :requested_end_time
        AND end_time > :requested_start_time
        AND expires_at > :time_now    
    """)

    result = db.session.execute(query, {
        'user_id': user_id,
        'spot_id': spot_id,
        'parking_lot_id': parking_lot_id,
        'booking_date': booking_date,
        'requested_end_time': end_time,
        'requested_start_time': start_time,
        'time_now': datetime.now(ZoneInfo("Europe/Nicosia")).isoformat()
    }).scalar()

    # If count is 0, the spot is available
    return result == 0