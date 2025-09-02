from config import db, ParkingSpot, redis_client, app
from datetime import datetime
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from config import socketio, redis_client
from booking.redis_utils import redis_smembers, redis_hget, redis_hset, redis_srem, redis_delete, redis_keys, redis_hdel

def validate_lease(reservation_id, spot_id, user_id):
    try:
        lease_data = redis_client.hgetall(f"lease_data:{reservation_id}")
        if not lease_data:
            return False

        return (lease_data.get('user_id') == str(user_id) and
                lease_data.get('spot_id') == str(spot_id))
    except Exception as e:
        print(f"Lease validation error: {str(e)}")
        return False

def is_spot_available(spot, parkingLotId, bookingDate, startTime, endTime):
    app.logger.info(f"is_spot_available called - spot: {spot.id}, lot: {parkingLotId}, date: {bookingDate}, time: {startTime}-{endTime}")

    lease_key = f"spot_lease:{spot.id}_{bookingDate}"
    current_lease = redis_client.get(lease_key)
    if current_lease and isinstance(current_lease, bytes):
        current_lease = current_lease.decode('utf-8')

    app.logger.info(f"Lease check - key: {lease_key}, current_lease: {current_lease}")

    if current_lease:
        app.logger.info(f"Spot {spot.id} has active lease: {current_lease}")
        return False

    from config import Booking
    conflict_count = Booking.query.filter(
        Booking.spot_id == spot.id,
        Booking.parking_lot_id == parkingLotId,
        Booking.bookingDate == bookingDate,
        Booking.startTime < endTime,
        Booking.endTime > startTime
    ).count()

    app.logger.info(f"Database conflict check - conflicts: {conflict_count}")

    return conflict_count == 0



def calculate_price(startTime, endTime, spotPricePerHour):
    start_dt = datetime.combine(datetime.today().date(), startTime)
    end_dt = datetime.combine(datetime.today().date(), endTime)
    duration_hours = (end_dt - start_dt).total_seconds() / 3600
    price_cents = int(round(duration_hours * spotPricePerHour * 100))
    return max(price_cents, 50)




def emit_to_relevant_rooms_about_booking(spot, booking_date, is_available, return_confirmation, start_time=None, end_time=None):
    try:
        target_room = f"lot_{spot.parkingLotId}_{booking_date}"
        print(f"\n=== Starting emission to {target_room} ===")
        print(f"Spot: {spot.id} | Available: {is_available} | Time Range: {start_time}-{end_time}")

        room_key = f"active_rooms:{target_room}"
        sids = redis_smembers(redis_client, room_key)
        if not sids:
            print(f"Room {target_room} not found")
            return False if return_confirmation else None

        recipients = 0
        for sid in sids:
            conn_data = redis_hget(redis_client, "active_connections", sid)
            if not conn_data:
                print(f"Missing connection data for {sid}")
                continue

            try:
                conn_start_str = conn_data.get('startTime')
                conn_end_str = conn_data.get('endTime')
                conn_start = datetime.strptime(conn_start_str, "%H:%M").time() if conn_start_str else None
                conn_end = datetime.strptime(conn_end_str, "%H:%M").time() if conn_end_str else None
            except (ValueError, TypeError) as e:
                print(f"Invalid time format for {sid}: {e}")
                continue

            send_update = True
            if start_time and end_time and conn_start and conn_end:
                time_overlap = not (end_time <= conn_start or start_time >= conn_end)

                if not is_available and not time_overlap:
                    send_update = False

                print(f"Client {sid} | Times: {conn_start_str}-{conn_end_str} | Overlap: {time_overlap} | Send: {send_update}")

            if send_update:
                socketio.emit('spot_update', {
                    'spotId': spot.id,
                    'available': is_available,
                    'timestamp': datetime.now(ZoneInfo("Europe/Nicosia")).isoformat()
                }, room=sid)
                recipients += 1

        print(f"=== Emission complete === Recipients: {recipients}\n")
        return True if return_confirmation else None

    except Exception as e:
        print(f"Emission error: {str(e)}")
        return False if return_confirmation else None
