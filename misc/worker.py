import time
import logging
from sqlalchemy import text
from config import db, app, ParkingSpot
from booking.routes.views import emit_to_relevant_rooms_about_booking


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def process_outbox():
    logger.info("Outbox worker started")

    """Worker that processes outbox events with SKIP LOCKED"""
    with app.app_context():
        while True:
            try:
                # Get next event to process with SKIP LOCKED
                query = text("""
                    SELECT * FROM outbox 
                    WHERE dispatched = false 
                    ORDER BY created_at LIMIT 1 
                    FOR UPDATE SKIP LOCKED
                """)

                event = db.session.execute(query).first()

                if not event:
                    time.sleep(1)  # No work, sleep briefly
                    continue

                # Process the event
                process_event(event)

                # Mark as dispatched
                db.session.execute(
                    text("UPDATE outbox SET dispatched = true, dispatched_at = NOW() WHERE id = :id"),
                    {"id": event.id}
                )
                db.session.commit()

            except Exception as e:
                logging.error(f"Outbox processing error: {str(e)}")
                db.session.rollback()
                time.sleep(5)  # Sleep longer on error

def process_event(event):
    """Process a single outbox event"""
    if event.event_type == 'booking_created':
        payload = event.payload
        # Emit WebSocket event
        spot = ParkingSpot.query.get(payload['spot_id'])
        emit_to_relevant_rooms_about_booking(
            spot=spot,
            booking_date=payload['booking_data']['booking_date'],
            is_available=False,
            return_confirmation=False,
            start_time=payload['booking_data']['start_time'],
            end_time=payload['booking_data']['end_time']
        )

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger.info("Starting outbox processing worker...")
    process_outbox()