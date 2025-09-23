from datetime import datetime
from booking.non_redis_cross_instance_worker.cross_instance_manager import broadcast_spot_update
from booking.redis.redis_utils import redis_smembers, redis_hget
from config import app, db, redis_client, ActiveConnection, socketio


def emit_to_relevant_rooms_about_booking(spot, booking_date, is_available, return_confirmation, start_time=None,
                                         end_time=None):
    try:
        # Check Redis availability
        redis_available = socketio.server.manager.redis_available
        app.logger.info(
            f"Starting emission - Redis: {redis_available}, Spot: {spot.id}, Date: {booking_date}, Available: {is_available}")

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
        app.logger.error(f"Emission error: {str(e)}", exc_info=True)
        return False if return_confirmation else None


def _emit_using_redis(target_room, spot, booking_date, is_available, start_time, end_time):
    room_key = f"active_rooms:{target_room}"
    sids = redis_smembers(redis_client, room_key)

    if not sids:
        app.logger.info(f"Room {target_room} not found or empty")
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

        conn_date = conn_data.get('bookingDate', '')
        if conn_date != booking_date:
            skipped_wrong_date += 1
            app.logger.info(f"Skipping {sid} - wrong date: {conn_date} != {booking_date}")
            continue

        if not _should_emit_based_on_time(conn_data, start_time, end_time, is_available):
            skipped_no_overlap += 1
            app.logger.info(f"Skipping {sid} - no time overlap")
            continue

        socketio.emit('spot_update', {'spotId': spot.id, 'available': is_available}, room=sid)
        recipients += 1
        app.logger.info(f"Emitted to {sid}")

    app.logger.info(
        f"Redis emission - Recipients: {recipients}, Skipped: {skipped_no_overlap} time, {skipped_wrong_date} date, {skipped_no_data} no data")
    return recipients > 0


def _emit_using_database_fallback(target_room, spot, booking_date, is_available, start_time, end_time):
    # Clean up expired connections first
    expired_count = ActiveConnection.query.filter(
        ActiveConnection.expires_at < datetime.now()
    ).delete()
    db.session.commit()

    if expired_count > 0:
        app.logger.info(f"Cleaned {expired_count} expired fallback connections")

    # Get active connections for this room
    active_connections = ActiveConnection.query.filter_by(room_name=target_room).all()
    app.logger.info(f"Found {len(active_connections)} fallback connections for {target_room}")

    emitted_count = 0
    skipped_no_overlap = 0
    skipped_wrong_date = 0

    for conn in active_connections:
        room_parts = conn.room_name.split('_')
        if len(room_parts) >= 3:
            conn_date = room_parts[2]
            if conn_date != str(booking_date):
                skipped_wrong_date += 1
                app.logger.info(f"Skipping {conn.socket_id} - wrong date: {conn_date} != {booking_date}")
                continue

        conn_data = {'startTime': conn.start_time, 'endTime': conn.end_time}
        if not _should_emit_based_on_time(conn_data, start_time, end_time, is_available):
            skipped_no_overlap += 1
            app.logger.info(f"Skipping {conn.socket_id} - no time overlap")
            continue

        socketio.emit('spot_update', {
            'spotId': spot.id,
            'available': is_available
        }, room=conn.socket_id)
        emitted_count += 1
        app.logger.info(f"Emitted to {conn.socket_id}")

    app.logger.info(
        f"Database fallback - Emitted: {emitted_count}, Skipped: {skipped_no_overlap} time, {skipped_wrong_date} date")
    return emitted_count > 0


def _should_emit_based_on_time(conn_data, start_time, end_time, is_available):
    if is_available:
        return True

    if not start_time or not end_time:
        return True

    conn_start_str = conn_data.get('startTime', '00:00')
    conn_end_str = conn_data.get('endTime', '23:59')

    try:
        conn_start = datetime.strptime(conn_start_str, "%H:%M").time()
        conn_end = datetime.strptime(conn_end_str, "%H:%M").time()

        def time_to_minutes(t):
            return t.hour * 60 + t.minute

        start_minutes = time_to_minutes(start_time)
        end_minutes = time_to_minutes(end_time)
        conn_start_minutes = time_to_minutes(conn_start)
        conn_end_minutes = time_to_minutes(conn_end)

        time_overlap = not (end_minutes <= conn_start_minutes or start_minutes >= conn_end_minutes)
        return time_overlap

    except (ValueError, TypeError):
        return True
