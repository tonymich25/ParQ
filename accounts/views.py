from flask import Blueprint

accounts_bp = Blueprint('accounts', __name__, template_folder='templates')


@accounts_bp.route('/register', methods=['GET', 'POST'])
def registration():



@accounts_bp.route('/login', methods=['GET', 'POST'])
def login():




@accounts_bp.route('/logout')
def logout():
    """