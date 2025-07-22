from config import app
from flask import render_template
import errors

# Registering errors
app.register_error_handler(400, errors.bad_request)
app.register_error_handler(401, errors.unauthorized)
app.register_error_handler(403, errors.forbidden)
app.register_error_handler(404, errors.not_found)
app.register_error_handler(405, errors.method_not_allowed)
app.register_error_handler(408, errors.request_timeout)
app.register_error_handler(500, errors.internal_server_error)
app.register_error_handler(502, errors.bad_gateway)
app.register_error_handler(503, errors.service_unavailable)
app.register_error_handler(504, errors.gateway_timeout)

@app.route('/')
def index():
    return render_template('index.html')

if __name__ == '__main__':
    app.run()