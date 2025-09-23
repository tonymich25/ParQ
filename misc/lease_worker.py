import time
import logging
from sqlalchemy import text
from booking.routes.views import emit_to_relevant_rooms_about_booking
from config import app, db, ParkingSpot
from booking.redis.redis_utils import redis_delete_lease

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def reconcile_expired_leases():
    """Worker to clean up expired leases"""
    with app.app_context():
        while True:
            try:
                # Find leases that should have expired
                query = text("""
                    SELECT * FROM spot_leases 
                    WHERE held_until < NOW() AND processed = false
                    ORDER BY held_until LIMIT 100
                    FOR UPDATE SKIP LOCKED
                """)

                expired_leases = db.session.execute(query).fetchall()

                for lease in expired_leases:
                    process_expired_lease(lease)

                time.sleep(1)  # Check every second

            except Exception as e:
                logging.error(f"Lease reconciliation error: {str(e)}")
                time.sleep(5)

def process_expired_lease(lease):
    """Process a single expired lease"""
    try:
        # Emit WebSocket event to make spot available
        spot = ParkingSpot.query.get(lease.spot_id)
        emit_to_relevant_rooms_about_booking(
            spot=spot,
            booking_date=lease.booking_date,
            is_available=True,
            return_confirmation=False,
            start_time=lease.start_time.strftime('%H:%M') if hasattr(lease.start_time,
                                                                     'strftime') else lease.start_time,
            end_time=lease.end_time.strftime('%H:%M') if hasattr(lease.end_time, 'strftime') else lease.end_time
        )

        # Clean up Redis lease
        redis_delete_lease(f"spot_lease:{lease.spot_id}", lease.reservation_id)

        # Mark as processed
        db.session.execute(
            text("UPDATE spot_leases SET processed = true WHERE id = :id"),
            {"id": lease.id}
        )
        db.session.commit()

    except Exception as e:
        db.session.rollback()
        logging.error(f"Failed to process expired lease {lease.id}: {str(e)}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger.info("Starting lease reconciliation worker...")
    reconcile_expired_leases()