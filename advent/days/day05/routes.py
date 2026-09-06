from flask import Blueprint, render_template

from ...navigation import get_day
from ...services.deepseek import get_model


bp = Blueprint("day05", __name__, url_prefix="/days/5")


@bp.get("/")
def index():
    return render_template(
        "days/day05.html",
        active_day=get_day(5),
        model=get_model(),
    )
