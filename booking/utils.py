from booking.cross_instance_manager import broadcast_spot_update
from config import db, ParkingSpot, redis_client, PendingBooking, ActiveConnection
from datetime import datetime
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from config import socketio, redis_client
from booking.redis_utils import redis_smembers, redis_hget, redis_hset, redis_srem, redis_delete, redis_keys, \
    redis_hdel, redis_health_check
from config import app
from flask import current_app


def validate_lease(reservation_id, spot_id, user_id):
    """Validate lease ownership and consistency using Redis metadata"""
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
    app.logger.info(
        f"🔍 is_spot_available called - spot: {spot.id}, lot: {parkingLotId}, date: {bookingDate}, time: {startTime}-{endTime}")

    # Check Redis health
    redis_available = socketio.server.manager.redis_available

    if redis_available:
        # 🎯 FIX: Add date to lease key to match acquisition format
        lease_key = f"spot_lease:{spot.id}_{bookingDate}"
        current_lease = redis_client.get(lease_key)
        if current_lease and isinstance(current_lease, bytes):
            current_lease = current_lease.decode('utf-8')

        app.logger.info(f"🔍 Lease check - key: {lease_key}, current_lease: {current_lease}")

        if current_lease:
            app.logger.info(f"❌ Spot {spot.id} has active lease: {current_lease}")
            return False  # Spot is leased
    else:
        app.logger.info("🔄 Redis unavailable - skipping lease check")

    from config import Booking
    # Check for conflicting bookings in database
    conflict_count = Booking.query.filter(
        Booking.spot_id == spot.id,
        Booking.parking_lot_id == parkingLotId,
        Booking.bookingDate == bookingDate,
        Booking.startTime < endTime,
        Booking.endTime > startTime
    ).count()

    app.logger.info(f"🔍 Database conflict check - conflicts: {conflict_count}")

    return conflict_count == 0


def calculate_price(startTime, endTime, spotPricePerHour):
    start_dt = datetime.combine(datetime.today().date(), startTime)
    end_dt = datetime.combine(datetime.today().date(), endTime)
    duration_hours = (end_dt - start_dt).total_seconds() / 3600
    price_cents = int(round(duration_hours * spotPricePerHour * 100))
    return max(price_cents, 50)


def emit_to_relevant_rooms_about_booking(spot, booking_date, is_available, return_confirmation, start_time=None,
                                         end_time=None):
    """Complete production emission function with Redis fallback - ALL FUNCTIONALITY INTACT"""
    try:
        # Check Redis availability
        redis_available = socketio.server.manager.redis_available
        app.logger.info(
            f"🎯 Starting emission - Redis: {redis_available}, Spot: {spot.id}, Date: {booking_date}, Available: {is_available}")

        # Broadcast to other instances
        broadcast_success = broadcast_spot_update(spot, booking_date, is_available, start_time, end_time)

        # Emit to local instance
        target_room = f"lot_{spot.parkingLotId}_{booking_date}"

        # Choose emission method based on Redis availability
        if redis_available:
            success = _emit_using_redis(target_room, spot, booking_date, is_available, start_time, end_time)
        else:
            success = _emit_using_database_fallback(target_room, spot, booking_date, is_available, start_time, end_time)

        return success if return_confirmation else None

    except Exception as e:
        app.logger.error(f"❌ Emission error: {str(e)}", exc_info=True)
        return False if return_confirmation else None


def _emit_using_redis(target_room, spot, booking_date, is_available, start_time, end_time):
    """Emit using Redis (primary path) - ALL FUNCTIONALITY PRESERVED"""
    room_key = f"active_rooms:{target_room}"
    sids = redis_smembers(redis_client, room_key)

    if not sids:
        app.logger.info(f"❌ Room {target_room} not found or empty")
        return False

    recipients = 0
    skipped_no_overlap = 0
    skipped_no_data = 0
    skipped_wrong_date = 0

    for sid in sids:
        conn_data = redis_hget(redis_client, "active_connections", sid)
        if not conn_data:
            skipped_no_data += 1
            continue

        # 🎯 CRITICAL: Check date match (prevents emissions to wrong dates)
        conn_date = conn_data.get('bookingDate', '')
        if conn_date != booking_date:
            skipped_wrong_date += 1
            app.logger.info(f"📅 Skipping {sid} - wrong date: {conn_date} != {booking_date}")
            continue

        # 🎯 CRITICAL: Check time overlap (core functionality)
        if not _should_emit_based_on_time(conn_data, start_time, end_time, is_available):
            skipped_no_overlap += 1
            app.logger.info(f"⏰ Skipping {sid} - no time overlap")
            continue

        # 🎯 EMIT TO CLIENT (existing functionality)
        socketio.emit('spot_update', {'spotId': spot.id, 'available': is_available}, room=sid)
        recipients += 1
        app.logger.info(f"✅ Emitted to {sid}")

    app.logger.info(
        f"📊 Redis emission - Recipients: {recipients}, Skipped: {skipped_no_overlap} time, {skipped_wrong_date} date, {skipped_no_data} no data")
    return recipients > 0


def _emit_using_database_fallback(target_room, spot, booking_date, is_available, start_time, end_time):
    """Emit using database fallback (Redis down path) - ALL FUNCTIONALITY PRESERVED"""
    # Clean up expired connections first
    expired_count = ActiveConnection.query.filter(
        ActiveConnection.expires_at < datetime.now()
    ).delete()
    db.session.commit()

    if expired_count > 0:
        app.logger.info(f"🧹 Cleaned {expired_count} expired fallback connections")

    # Get active connections for this room
    active_connections = ActiveConnection.query.filter_by(room_name=target_room).all()
    app.logger.info(f"🔍 Found {len(active_connections)} fallback connections for {target_room}")

    emitted_count = 0
    skipped_no_overlap = 0
    skipped_wrong_date = 0

    for conn in active_connections:
        # 🎯 CRITICAL: Extract date from room name and check match
        room_parts = conn.room_name.split('_')
        if len(room_parts) >= 3:
            conn_date = room_parts[2]  # lot_1_2025-09-15 → 2025-09-15
            if conn_date != str(booking_date):
                skipped_wrong_date += 1
                app.logger.info(f"📅 Skipping {conn.socket_id} - wrong date: {conn_date} != {booking_date}")
                continue

        # 🎯 CRITICAL: Check time overlap (core functionality)
        conn_data = {'startTime': conn.start_time, 'endTime': conn.end_time}
        if not _should_emit_based_on_time(conn_data, start_time, end_time, is_available):
            skipped_no_overlap += 1
            app.logger.info(f"⏰ Skipping {conn.socket_id} - no time overlap")
            continue

        # 🎯 EMIT TO CLIENT (existing functionality)
        socketio.emit('spot_update', {
            'spotId': spot.id,
            'available': is_available
        }, room=conn.socket_id)
        emitted_count += 1
        app.logger.info(f"✅ Emitted to {conn.socket_id}")

    app.logger.info(
        f"📊 Database fallback - Emitted: {emitted_count}, Skipped: {skipped_no_overlap} time, {skipped_wrong_date} date")
    return emitted_count > 0


def _should_emit_based_on_time(conn_data, start_time, end_time, is_available):
    """COMPLETE time overlap checking - ALL ORIGINAL LOGIC PRESERVED"""
    if is_available:  # Available updates go to everyone
        return True

    if not start_time or not end_time:  # No time range specified
        return True

    # Extract time info from connection
    conn_start_str = conn_data.get('startTime', '00:00')
    conn_end_str = conn_data.get('endTime', '23:59')

    try:
        conn_start = datetime.strptime(conn_start_str, "%H:%M").time()
        conn_end = datetime.strptime(conn_end_str, "%H:%M").time()

        # Convert to minutes for comparison (no timezone issues)
        def time_to_minutes(t):
            return t.hour * 60 + t.minute

        start_minutes = time_to_minutes(start_time)
        end_minutes = time_to_minutes(end_time)
        conn_start_minutes = time_to_minutes(conn_start)
        conn_end_minutes = time_to_minutes(conn_end)

        # Check for time overlap (EXACT SAME LOGIC AS BEFORE)
        time_overlap = not (end_minutes <= conn_start_minutes or start_minutes >= conn_end_minutes)
        return time_overlap

    except (ValueError, TypeError):
        # If time parsing fails, default to emitting (conservative approach)
        return True


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
        current_app.logger.error(f"❌ Failed to store pending booking: {str(e)}")
        return False


def delete_pending_booking(reservation_id):
    """Delete from pending_bookings table"""
    try:
        PendingBooking.query.filter_by(reservation_id=reservation_id).delete()
        db.session.commit()
        return True
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"❌ Failed to delete pending booking: {str(e)}")
        return False


def get_pending_booking(reservation_id):
    """Retrieve pending booking data from database"""
    try:
        # Clean up expired bookings first
        expired_count = PendingBooking.query.filter(PendingBooking.expires_at < datetime.now()).delete()
        if expired_count > 0:
            current_app.logger.info(f"🧹 Cleaned up {expired_count} expired pending bookings")
        db.session.commit()

        # Get the booking
        pending_booking = PendingBooking.query.filter_by(reservation_id=reservation_id).first()
        if pending_booking:
            current_app.logger.info(f"✅ Retrieved pending booking {reservation_id}")
            return {
                'user_id': pending_booking.user_id,
                'parking_lot_id': pending_booking.parking_lot_id,
                'spot_id': pending_booking.spot_id,
                'booking_date': pending_booking.booking_date,
                'start_time': pending_booking.start_time,
                'end_time': pending_booking.end_time,
                'amount': pending_booking.amount
            }
        current_app.logger.warning(f"⚠️ Pending booking not found: {reservation_id}")
        return None

    except Exception as e:
        current_app.logger.error(f"❌ Failed to retrieve pending booking: {str(e)}")
        return None

