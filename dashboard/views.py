from flask import render_template, Blueprint
from flask_login import login_required

dashboard_bp = Blueprint('dashboard', __name__, template_folder='dashboard')


@dashboard_bp.route('/dashboard', methods=['GET', 'POST'])
@login_required
def dashboard():


    return render_template('dashboard/dashboard.html')