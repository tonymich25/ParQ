import json
import redis
from config import redis_client, app

def redis_health_check():
    try:
        return redis_client.ping()
    except redis.RedisError:
        return False

LEASE_ACQUIRE_SCRIPT = """
return redis.call('SET', KEYS[1], ARGV[1], 'NX', 'EX', ARGV[2])
"""

LEASE_RENEW_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""

LEASE_DELETE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""

def init_redis_scripts():
    global lease_acquire_script, lease_renew_script, lease_delete_script
    try:
        lease_acquire_script = redis_client.register_script(LEASE_ACQUIRE_SCRIPT)
        lease_renew_script = redis_client.register_script(LEASE_RENEW_SCRIPT)
        lease_delete_script = redis_client.register_script(LEASE_DELETE_SCRIPT)
        app.logger.info("Redis scripts registered successfully")
    except Exception as e:
        app.logger.error(f"Failed to register Redis scripts: {str(e)}")
        raise
def redis_acquire_lease(key, value, ttl):
    try:
        result = lease_acquire_script(keys=[key], args=[value, ttl])
        return result is not None  # Convert to boolean
    except redis.RedisError as e:
        print(f"Redis lease acquire error for key {key}: {str(e)}")
        return False

def redis_renew_lease(key, value, ttl):
    try:
        return lease_renew_script(keys=[key], args=[value, ttl]) == 1
    except redis.RedisError as e:
        print(f"Redis lease renew error for key {key}: {str(e)}")
        return False

def redis_delete_lease(key, value):
    try:
        return lease_delete_script(keys=[key], args=[value]) == 1
    except redis.RedisError as e:
        print(f"Redis lease delete error for key {key}: {str(e)}")
        return False

def redis_get(key):
    """Safe get with error handling"""
    try:
        value = redis_client.get(key)
        return value.decode('utf-8') if value else None
    except redis.RedisError as e:
        print(f"Redis GET error for key {key}: {str(e)}")
        return None

def redis_sadd(key, value):
    try:
        return redis_client.sadd(key, value)
    except redis.RedisError as e:
        print(f"Redis SADD error for key {key}: {str(e)}")
        return 0

def redis_srem(key, value):
    try:
        return redis_client.srem(key, value)
    except redis.RedisError as e:
        print(f"Redis SREM error for key {key}: {str(e)}")
        return 0

def redis_smembers(key):
    try:
        members = redis_client.smembers(key)
        return {m.decode('utf-8') for m in members} if members else set()
    except redis.RedisError as e:
        print(f"Redis SMEMBERS error for key {key}: {str(e)}")
        return set()

def redis_hset(key, field, value):
    try:
        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        return redis_client.hset(key, field, value)
    except redis.RedisError as e:
        print(f"Redis HSET error for key {key}, field {field}: {str(e)}")
        return 0

def redis_hget(key, field):
    try:
        value = redis_client.hget(key, field)
        if value:
            try:
                return json.loads(value.decode('utf-8'))
            except json.JSONDecodeError:
                return value.decode('utf-8')
        return None
    except redis.RedisError as e:
        print(f"Redis HGET error for key {key}, field {field}: {str(e)}")
        return None

def redis_hgetall(key):
    result = redis_client.hgetall(key)
    return {k.decode('utf-8'): json.loads(v.decode('utf-8')) if v.decode('utf-8').startswith('{') else v.decode('utf-8')
            for k, v in result.items()}

def redis_hdel(key, field):
    try:
        return redis_client.hdel(key, field)
    except redis.RedisError as e:
        print(f"Redis HDEL error for key {key}, field {field}: {str(e)}")
        return 0

def redis_delete(key):
    try:
        return redis_client.delete(key)
    except redis.RedisError as e:
        print(f"Redis DELETE error for key {key}: {str(e)}")
        return 0

def redis_keys(pattern):
    try:
        return [key.decode('utf-8') for key in redis_client.keys(pattern)]
    except redis.RedisError as e:
        print(f"Redis KEYS error for pattern {pattern}: {str(e)}")
        return []
