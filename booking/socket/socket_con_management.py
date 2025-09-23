import json
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta
from flask import request, current_app
from flask_login import current_user
from flask_socketio import emit, leave_room, join_room
from booking.redis.redis_utils import redis_hset, redis_keys, redis_safe_release_lease, redis_srem, redis_smembers, redis_delete, redis_hdel, redis_hget, redis_sadd
from config import socketio, redis_client, ActiveConnection, db, app


@socketio.on('connect')
def handle_connect():
    print("Client connected: ", request.sid)
    redis_hset(redis_client, "active_connections", request.sid, {  # ADD redis_client
        'connected_at': datetime.now(ZoneInfo("Europe/Nicosia")).isoformat(),
        'rooms': '[]',
        'user_id': str(current_user.get_id()) if current_user.is_authenticated else 'anonymous'
    })


@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    current_app.logger.info(f"Client disconnecting: {sid}")

    room_keys = redis_keys(redis_client, "active_rooms:*")

    conn_data = redis_hget(redis_client, "active_connections", sid) or {}
    current_app.logger.info(f"Connection data: {conn_data}")

    reservation_id = conn_data.get('reservation_id')

    if reservation_id:
        current_app.logger.info(f"Checking lease data for reservation: {reservation_id}")
        lease_data = redis_client.hgetall(f"lease_data:{reservation_id}")

        if lease_data:
            # Check if this is a payment-related lease
            is_payment_lease = any(key in [b'stripe_session_id', 'stripe_session_id', b'payment_context', 'payment_context']
                                 for key in lease_data.keys())

            if is_payment_lease:
                current_app.logger.info(f"Payment lease detected - preserving {reservation_id}")
                # Don't clean up - the payment_success route will handle cleanup
            else:
                current_app.logger.info(f"🗑Cleaning up non-payment lease: {reservation_id}")
                try:
                    spot_id = lease_data.get(b'spot_id', b'').decode() if b'spot_id' in lease_data else lease_data.get('spot_id', '')
                    booking_date = lease_data.get(b'booking_date', b'').decode() if b'booking_date' in lease_data else lease_data.get('booking_date', '')

                    if spot_id and booking_date:
                        lease_key = f"spot_lease:{spot_id}_{booking_date}"
                        redis_safe_release_lease(redis_client, lease_key, reservation_id)
                        current_app.logger.info(f"Cleaned up lease {reservation_id} for spot {spot_id}")
                except Exception as e:
                    current_app.logger.error(f"Lease cleanup error: {str(e)}")
    else:
        current_app.logger.info("ℹNo reservation ID found in connection data")

    rooms_data = conn_data.get('rooms', '[]')
    try:
        if isinstance(rooms_data, str):
            rooms = json.loads(rooms_data)
        elif isinstance(rooms_data, list):
            rooms = rooms_data
        else:
            rooms = []
    except (json.JSONDecodeError, TypeError):
        rooms = []

    current_app.logger.info(f"Client was in {len(rooms)} rooms: {rooms}")

    if rooms:
        for room_name in rooms:
            if isinstance(room_name, str):
                redis_srem(redis_client, f"active_rooms:{room_name}", sid)
                if not redis_smembers(redis_client, f"active_rooms:{room_name}"):
                    redis_delete(redis_client, f"active_rooms:{room_name}")
                    current_app.logger.info(f"🗑Deleted empty room: {room_name}")

    redis_hdel(redis_client, "active_connections", sid)
    current_app.logger.info(f"Removed connection data for sid: {sid}")


@socketio.on('subscribe')
def handle_subscribe(data):
    try:
        parking_lot_id = data.get('parkingLotId')
        booking_date = data.get('bookingDate')
        start_time = data.get('startTime', '00:00')
        end_time = data.get('endTime', '23:59')

        if not parking_lot_id or not booking_date:
            emit('subscription_error', {'message': 'Missing required fields'})
            return

        new_room_name = f"lot_{parking_lot_id}_{booking_date}"
        conn_data = redis_hget(redis_client, "active_connections", request.sid) or {}

        rooms_data = conn_data.get('rooms', '[]')
        try:
            if isinstance(rooms_data, str):
                current_rooms = json.loads(rooms_data)
            elif isinstance(rooms_data, list):
                current_rooms = rooms_data
            else:
                current_rooms = []
        except (json.JSONDecodeError, TypeError):
            current_rooms = []

        rooms_to_leave = []
        for room in current_rooms[:]:
            if isinstance(room, str) and room.startswith('lot_'):
                room_parts = room.split('_')
                if len(room_parts) >= 2 and room_parts[1] == str(parking_lot_id):
                    rooms_to_leave.append(room)
                    leave_room(room)
                    redis_srem(redis_client, f"active_rooms:{room}", request.sid)
                    if not redis_smembers(redis_client, f"active_rooms:{room}"):
                        redis_delete(redis_client, f"active_rooms:{room}")
                    current_rooms.remove(room)

        join_room(new_room_name)
        redis_sadd(redis_client, f"active_rooms:{new_room_name}", request.sid)
        current_rooms.append(new_room_name)

        conn_data.update({
            'parkingLotId': str(parking_lot_id),
            'bookingDate': booking_date,
            'startTime': start_time,
            'endTime': end_time,
            'rooms': json.dumps(current_rooms)
        })

        redis_hset(redis_client, "active_connections", request.sid, conn_data)

        # Store in database fallback
        fallback_conn = ActiveConnection.query.filter_by(socket_id=request.sid).first()
        if fallback_conn:
            # Refresh TTL on existing connection
            fallback_conn.room_name = new_room_name
            fallback_conn.start_time = start_time
            fallback_conn.end_time = end_time
            fallback_conn.expires_at = datetime.now() + timedelta(minutes=5)
        else:
            # Create new connection with TTL
            fallback_conn = ActiveConnection(
                socket_id=request.sid,
                user_id=current_user.get_id(),
                room_name=new_room_name,
                start_time=start_time,
                end_time=end_time
            )
            db.session.add(fallback_conn)

        db.session.commit()

        app.logger.info(f"Client {request.sid} subscribed to {new_room_name}")

    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Subscription error for {request.sid}: {str(e)}")
        emit('subscription_error', {'message': 'Internal server error'})


def disconnect_user(session):
    user_id = session.metadata['user_id']
    current_app.logger.info(f"disconnect_user called for user_id: {user_id}")

    # Iterate through all active rooms using Redis pattern matching
    room_keys = redis_keys(redis_client, "active_rooms:*")
    current_app.logger.info(f"Found {len(room_keys)} active rooms")

    for room_key in room_keys:
        room_name = room_key.replace("active_rooms:", "")
        sids = redis_smembers(redis_client, room_key)
        current_app.logger.info(f"Room {room_name} has {len(sids)} connections")

        # Check if any socket in this room belongs to the user
        user_sids = {sid for sid in sids if sid.startswith(f"{user_id}_")}
        current_app.logger.info(f"User {user_id} has {len(user_sids)} connections in room {room_name}")

        for sid in user_sids:
            # Check if this connection has an active payment lease
            conn_data = redis_hget("active_connections", sid) or {}
            reservation_id = conn_data.get('reservation_id')

            if reservation_id:
                # Check if this is a payment-related lease
                lease_data = redis_client.hgetall(f"lease_data:{reservation_id}")
                if lease_data and any(key in [b'stripe_session_id', 'stripe_session_id'] for key in lease_data.keys()):
                    current_app.logger.info(f"Preserving payment lease {reservation_id} for sid {sid}")
                    continue

            current_app.logger.info(f"Disconnecting sid {sid} from room {room_name}")
            emit('payment_complete', {}, room=sid)
            socketio.disconnect(sid)
            redis_srem(room_key, sid)