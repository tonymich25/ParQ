import threading
import redis
from config import redis_client, socketio, app
from booking.utils import emit_to_relevant_rooms_about_booking
from config import ParkingSpot

def start_redis_expiration_listener():
    def expiration_listener():
        try:
            pubsub_redis = redis.from_url(app.config['REDIS_URL'])
            pubsub = pubsub_redis.pubsub()

            pubsub.psubscribe('__keyevent@0__:expired')

            print("Redis expiration listener started")

            for message in pubsub.listen():
                if message['type'] == 'pmessage':
                    expired_key = message['data'].decode('utf-8')

                    if expired_key.startswith('spot_lease:'):
                        key_parts = expired_key.split(':')
                        if len(key_parts) < 2:
                            continue

                        spot_date_parts = key_parts[1].split('_')
                        if len(spot_date_parts) < 2:
                            continue

                        spot_id = spot_date_parts[0]
                        booking_date = '_'.join(spot_date_parts[1:])

                        print(f"Lease expired for spot {spot_id} on {booking_date}")

                        spot = ParkingSpot.query.get(spot_id)
                        if spot:
                            emit_to_relevant_rooms_about_booking(
                                spot,
                                booking_date,
                                True,
                                False
                            )

        except Exception as e:
            print(f"Redis expiration listener error: {str(e)}")

    thread = threading.Thread(target=expiration_listener, daemon=True)
    thread.start()

@app.before_first_request
def startup():
    start_redis_expiration_listener()
