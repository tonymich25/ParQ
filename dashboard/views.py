from flask import Blueprint

dashboard_bp = Blueprint('dashboard', __name__, template_folder='dashboard')


@dashboard_bp.route('/dashboard', methods=['GET', 'POST'])
#@login_required
def dashboard():
