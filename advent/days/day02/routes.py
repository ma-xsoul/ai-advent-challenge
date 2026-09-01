from flask import Blueprint, render_template

from ...navigation import get_day


bp = Blueprint("day02", __name__, url_prefix="/days/2")


@bp.get("/")
def index():
    return render_template("days/day02.html", active_day=get_day(2))
