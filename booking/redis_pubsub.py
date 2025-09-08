import threading
import redis
from flask import current_app
from config import redis_client, socketio, app
from booking.utils import emit_to_relevant_rooms_about_booking
from config import ParkingSpot, db, app


def start_redis_expiration_listener():
    """Start a thread to listen for Redis key expiration events"""

    def expiration_listener():
        try:
            # 🎯 CRITICAL: Create app context for the thread
            with app.app_context():
                app.logger.info("🔔 Starting Redis expiration listener...")

                # Configure Redis to send expiry notifications
                try:
                    redis_client.config_set('notify-keyspace-events', 'Ex')
                    app.logger.info("✅ Redis keyspace notifications enabled")
                except redis.exceptions.ConnectionError:
                    app.logger.warning("⚠️ Redis unavailable - expiration listener paused")
                    return  # Exit thread if Redis is down
                except redis.exceptions.ResponseError:
                    app.logger.warning("⚠️ Redis keyspace notifications may need server config")

                # Create a new connection for pub/sub (can't use the same connection)
                try:
                    pubsub_redis = redis.from_url(current_app.config['REDIS_URL'])
                    pubsub = pubsub_redis.pubsub()
                except redis.exceptions.ConnectionError:
                    app.logger.warning("⚠️ Redis unavailable - expiration listener paused")
                    return  # Exit thread if Redis is down

                # Subscribe to key expiration events
                pubsub.psubscribe('__keyevent@0__:expired')
                app.logger.info("✅ Subscribed to Redis expiry events")

                app.logger.info("🔔 Redis expiration listener started")

                for message in pubsub.listen():
                    if message['type'] == 'pmessage':
                        expired_key = message['data'].decode('utf-8')
                        app.logger.info(f"🔔 Received expiry event: {expired_key}")

                        if expired_key.startswith('spot_lease:'):
                            # Extract spot_id and date from key: spot_lease:{spot_id}_{date}
                            key_parts = expired_key.split(':')
                            if len(key_parts) < 2:
                                continue

                            spot_date_parts = key_parts[1].split('_')
                            if len(spot_date_parts) < 2:
                                continue

                            spot_id = spot_date_parts[0]
                            booking_date = '_'.join(spot_date_parts[1:])

                            app.logger.info(f"🔔 Lease expired for spot {spot_id} on {booking_date}")

                            # Get the spot from database
                            with app.app_context():
                                spot = ParkingSpot.query.get(spot_id)
                                if spot:
                                    # Emit update that spot is now available
                                    emit_to_relevant_rooms_about_booking(
                                        spot,
                                        booking_date,
                                        True,  # available
                                        False  # don't return confirmation
                                    )
                                    app.logger.info(f"🔔 Emitted expiry update for spot {spot_id}")
                                else:
                                    app.logger.warning(f"⚠️ Spot not found: {spot_id}")

        except Exception as e:
            app.logger.error(f"❌ Redis expiration listener error: {str(e)}", exc_info=True)

    # Start the listener in a separate thread
    thread = threading.Thread(target=expiration_listener, daemon=True)
    thread.start()
    app.logger.info("✅ Redis expiry listener thread started")
