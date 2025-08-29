import json
import redis
from config import redis_client

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
