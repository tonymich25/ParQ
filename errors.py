from flask import render_template

def bad_request(e):
    return render_template('errors/400.html'), 400

def unauthorized(e):
    return render_template('errors/401.html'), 401

def forbidden(e):
    return render_template('errors/403.html'), 403

def not_found(e):
    return render_template('errors/404.html'), 404

def method_not_allowed(e):
    return render_template('errors/405.html'), 405

def request_timeout(e):
    return render_template('errors/408.html'), 408

def internal_server_error(e):
    return render_template('errors/500.html'), 500

def bad_gateway(e):
    return render_template('errors/502.html'), 502

def service_unavailable(e):
    return render_template('errors/503.html'), 503

def gateway_timeout(e):
    return render_template('errors/504.html'), 504
