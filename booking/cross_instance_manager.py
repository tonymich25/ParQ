import threading
import time
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from config import app, db, ParkingSpot, PendingBooking  # 🎯 IMPORT APP FROM CONFIG

logger = logging.getLogger(__name__)


class CrossInstanceManager:
    def __init__(self):
        self.polling_interval = 3
        self.polling_thread = None
        self.running = False
        self.last_processed_booking_ids = set()

    def start(self):
        if self.polling_thread and self.polling_thread.is_alive():
            logger.info("⚠️ Polling thread already running")
            return

        self.running = True
        self.polling_thread = threading.Thread(target=self._poll_database, daemon=True)
        self.polling_thread.start()
        logger.info("✅ Database polling thread started")

    def _poll_database(self):
        """Poll database for recent bookings from OTHER instances"""
        logger.info("🔄 Polling thread started - beginning database checks")
        while self.running:
            try:
                # 🎯 USE APP FROM CONFIG (NO NEED TO PASS IT)
                with app.app_context():
                    logger.debug("🔍 Checking for recent bookings from other instances...")
                    self._check_recent_bookings_from_other_instances()
                time.sleep(self.polling_interval)
            except Exception as e:
                logger.error(f"❌ Polling error: {str(e)}")
                time.sleep(self.polling_interval)

    def _check_recent_bookings_from_other_instances(self):
        """Check for recent bookings made on OTHER instances"""
        try:
            # Look for bookings created in the last 5 seconds
            recent_time = datetime.now(ZoneInfo("Europe/Nicosia")) - timedelta(seconds=5)

            recent_bookings = PendingBooking.query.filter(
                PendingBooking.created_at >= recent_time
            ).all()

            logger.debug(f"🔍 Found {len(recent_bookings)} recent bookings")

            # 🎯 FIX: current_user is None in background threads, so skip user check
            # Just process ALL recent bookings from the last few seconds
            for booking in recent_bookings:
                booking_id = f"pending_{booking.id}"

                if booking_id not in self.last_processed_booking_ids:
                    self.last_processed_booking_ids.add(booking_id)
                    self._process_booking_from_other_instance(booking)
                    logger.info(f"✅ Processed cross-instance booking: {booking.id}")

        except Exception as e:
            logger.error(f"❌ Booking check error: {str(e)}")

    def _process_booking_from_other_instance(self, booking):
        """Process a booking that came from another instance"""
        try:
            logger.info(f"🎯 Processing booking from other instance: {booking.id}")
            spot = ParkingSpot.query.get(booking.spot_id)
            if not spot:
                logger.warning(f"⚠️ Spot not found: {booking.spot_id}")
                return

            # 🎯 Use the actual booking's time information
            from booking.utils import emit_to_relevant_rooms_about_booking

            emit_to_relevant_rooms_about_booking(
                spot,
                booking.booking_date,
                False,  # available=false (spot was taken)
                False,  # don't return confirmation
                booking.start_time,  # 🎯 ACTUAL booking start time
                booking.end_time  # 🎯 ACTUAL booking end time
            )

            logger.info(f"📡 Emitted update for spot {booking.spot_id} from other instance")

        except Exception as e:
            logger.error(f"❌ Booking processing error: {str(e)}")

    def broadcast_spot_update(self, spot, booking_date, available, start_time=None, end_time=None):
        """Broadcast spot update to other instances"""
        logger.info(f"📍 Cross-instance broadcast requested for spot {spot.id}")
        return True


# Global instance
cross_instance_manager = CrossInstanceManager()


def init_cross_instance_messaging():
    """Initialize cross-instance messaging"""
    logger.info("🎯 Initializing cross-instance messaging...")
    cross_instance_manager.start()


def broadcast_spot_update(spot, booking_date, available, start_time=None, end_time=None):
    logger.info(f"📤 Broadcasting spot update: {spot.id}, available={available}")
    return cross_instance_manager.broadcast_spot_update(spot, booking_date, available, start_time, end_time)