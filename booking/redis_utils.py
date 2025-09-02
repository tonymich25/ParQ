import json
import redis

from config import redis_client


def redis_health_check(redis_client):
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

LEASE_SAFE_RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    redis.call('DEL', KEYS[1])
    redis.call('DEL', 'lease_data:' .. ARGV[1])
    return 1
else
    return 0
end
"""

def init_redis_scripts(redis_client, app):
    global lease_acquire_script, lease_renew_script, lease_delete_script, lease_safe_release_script
    try:
        lease_acquire_script = redis_client.register_script(LEASE_ACQUIRE_SCRIPT)
        lease_renew_script = redis_client.register_script(LEASE_RENEW_SCRIPT)
        lease_delete_script = redis_client.register_script(LEASE_DELETE_SCRIPT)
        lease_safe_release_script = redis_client.register_script(LEASE_SAFE_RELEASE_SCRIPT)
        app.logger.info("Redis scripts registered successfully")
    except Exception as e:
        app.logger.error(f"Failed to register Redis scripts: {str(e)}")
        raise

def redis_acquire_lease(redis_client, key, value, ttl):
    try:
        print(f"Redis SET {key} {value} NX EX {ttl}")
        result = lease_acquire_script(keys=[key], args=[value, ttl])
        print(f"Redis SET result: {result}")
        return result is not None
    except redis.RedisError as e:
        print(f"Redis lease acquire error for key {key}: {str(e)}")
        return False

def redis_renew_lease(redis_client, key, value, ttl):
    try:
        return lease_renew_script(keys=[key], args=[value, ttl]) == 1
    except redis.RedisError as e:
        print(f"Redis lease renew error for key {key}: {str(e)}")
        return False

def redis_delete_lease(redis_client, key, value):
    try:
        return lease_delete_script(keys=[key], args=[value]) == 1
    except redis.RedisError as e:
        print(f"Redis lease delete error for key {key}: {str(e)}")
        return False

def redis_safe_release_lease(redis_client, key, value):
    try:
        result = lease_safe_release_script(keys=[key], args=[value])
        return result == 1
    except redis.RedisError as e:
        print(f"Redis safe release error for key {key}: {str(e)}")
        redis_client.delete(key)
        redis_client.delete(f"lease_data:{value}")
        return True

def redis_get(redis_client, key):
    try:
        value = redis_client.get(key)
        if value and isinstance(value, bytes):
            return value.decode('utf-8')
        return value
    except redis.RedisError as e:
        print(f"Redis GET error for key {key}: {str(e)}")
        return None

def redis_sadd(redis_client, key, value):
    try:
        return redis_client.sadd(key, value)
    except redis.RedisError as e:
        print(f"Redis SADD error for key {key}: {str(e)}")
        return 0

def redis_srem(redis_client, key, value):
    try:
        return redis_client.srem(key, value)
    except redis.RedisError as e:
        print(f"Redis SREM error for key {key}: {str(e)}")
        return 0

def redis_smembers(redis_client, key):
    try:
        members = redis_client.smembers(key)
        return {m.decode('utf-8') for m in members} if members else set()
    except redis.RedisError as e:
        print(f"Redis SMEMBERS error for key {key}: {str(e)}")
        return set()

def redis_hset(redis_client, key, field, value):
    try:
        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        return redis_client.hset(key, field, value)
    except redis.RedisError as e:
        print(f"Redis HSET error for key {key}, field {field}: {str(e)}")
        return 0

def redis_hget(redis_client, key, field):
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
    """Safe hgetall that handles both bytes and string data"""
    try:
        result = redis_client.hgetall(key)
        decoded_result = {}

        for k, v in result.items():
            key_str = k.decode('utf-8') if isinstance(k, bytes) else k
            if isinstance(v, bytes):
                try:
                    decoded_result[key_str] = json.loads(v.decode('utf-8'))
                except json.JSONDecodeError:
                    decoded_result[key_str] = v.decode('utf-8')
            else:
                decoded_result[key_str] = v

        return decoded_result
    except redis.RedisError as e:
        print(f"Redis HGETALL error for key {key}: {str(e)}")
        return {}

def redis_hdel(redis_client, key, field):
    try:
        return redis_client.hdel(key, field)
    except redis.RedisError as e:
        print(f"Redis HDEL error for key {key}, field {field}: {str(e)}")
        return 0

def redis_delete(redis_client, key):
    try:
        return redis_client.delete(key)
    except redis.RedisError as e:
        print(f"Redis DELETE error for key {key}: {str(e)}")
        return 0

def redis_keys(redis_client, pattern):
    try:
        return [key.decode('utf-8') for key in redis_client.keys(pattern)]
    except redis.RedisError as e:
        print(f"Redis KEYS error for pattern {pattern}: {str(e)}")
        return []


def redis_safe_release_lease(redis_client, key, value):
    try:
        result = lease_safe_release_script(keys=[key], args=[value])
        return result == 1
    except redis.RedisError as e:
        print(f"Redis safe release error for key {key}: {str(e)}")
        redis_client.delete(key)
        redis_client.delete(f"lease_data:{value}")
        return True
