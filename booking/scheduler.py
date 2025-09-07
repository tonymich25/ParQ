from datetime import datetime
from sched import scheduler

import redis
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from flask import current_app

from booking import booking_service
from config import app, PendingBooking, db, redis_client


def init_scheduler():
    """Initialize background scheduler for cleanup tasks"""
    scheduler = BackgroundScheduler()

    # Clean up expired pending bookings every hour
    scheduler.add_job(
        cleanup_expired_pending_bookings,
        trigger=IntervalTrigger(hours=1),
        id='cleanup_pending_bookings',
        replace_existing=True
    )

    # 🎯 ADD THIS - Redis recovery check every 30 seconds
    scheduler.add_job(
        check_redis_recovery,
        trigger=IntervalTrigger(seconds=30),
        id='redis_recovery_check',
        replace_existing=True
    )

    scheduler.start()
    current_app.logger.info("✅ Background scheduler started with Redis recovery checks")


def cleanup_expired_pending_bookings():
    """Clean up expired pending bookings"""
    try:
        with app.app_context():
            expired_count = PendingBooking.query.filter(PendingBooking.expires_at < datetime.now()).delete()
            db.session.commit()
            if expired_count > 0:
                current_app.logger.info(f"🧹 Cleaned up {expired_count} expired pending bookings")
    except Exception as e:
        current_app.logger.error(f"❌ Failed to clean up expired pending bookings: {str(e)}")
        db.session.rollback()

# In scheduler.py or somewhere appropriate
def check_redis_recovery():
    if booking_service.redis_circuit_open:
        try:
            if redis_client.ping():
                booking_service.redis_circuit_open = False  # Close circuit if Redis is back
                current_app.logger.info("✅ Redis recovered! Circuit closed.")
        except redis.exceptions.ConnectionError:
            # Redis still down - circuit REMAINS open (no need to set it again)
            current_app.logger.warning("🔴 Redis still down - circuit remains open")
            # 🚨 REMOVE THIS LINE: booking_service.redis_circuit_open = True



# Call this during application startup
init_scheduler()