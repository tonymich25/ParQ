from flask import Blueprint, render_template

book_bp = Blueprint('book', __name__, template_folder='templates')


@book_bp.route('/book', methods=['GET', 'POST'])
def book():


    return render_template('book/book.html')


