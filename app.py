from __future__ import annotations

import os
from typing import Any

from flask import Flask, flash, redirect, render_template, request, url_for

SITE_CONTENT: dict[str, Any] = {
    "event_name": "Sea Shell Weekend",
    "tagline": "A soft blue-and-cream coastal escape for sun, supper, and slow mornings.",
    "event_date": "August 14-16, 2026",
    "location": "Bluewater Cove, Prince Edward Island",
    "hero_stats": [
        {"label": "Stay", "value": "2 nights seaside"},
        {"label": "Mood", "value": "Barefoot, breezy, candlelit"},
        {"label": "Palette", "value": "Sea glass, shell, sand"},
    ],
    "itinerary": [
        {
            "day": "Friday",
            "title": "Arrival and golden-hour welcome",
            "items": [
                "4:00 PM check-in and room drop",
                "6:30 PM oyster bar and sparkling mocktails",
                "8:00 PM beach walk at sunset",
            ],
        },
        {
            "day": "Saturday",
            "title": "Slow brunch and coastal exploring",
            "items": [
                "9:00 AM veranda brunch",
                "12:00 PM shell collecting and local shops",
                "7:00 PM candlelit seafood dinner",
            ],
        },
        {
            "day": "Sunday",
            "title": "Coffee, keepsakes, and farewell",
            "items": [
                "8:30 AM coffee cart and pastries",
                "10:00 AM Polaroids and memory table",
                "11:00 AM checkout",
            ],
        },
    ],
    "packing_list": [
        "Flowy layers for cool evenings",
        "Flat sandals or espadrilles",
        "Swimwear and a light cover-up",
        "A straw tote for market wandering",
        "A cardigan for late-night dock chats",
        "Your favorite camera or film snapshots",
    ],
    "outfit_notes": [
        {
            "title": "Arrival whites",
            "copy": "Think shell-toned linen, soft knits, and simple gold details.",
        },
        {
            "title": "Saturday blue hour",
            "copy": "Coastal blues, airy cotton, and textures that move in the wind.",
        },
        {
            "title": "Sunday market set",
            "copy": "Relaxed denim, cream layers, and a woven bag for small finds.",
        },
    ],
    "hotel": {
        "name": "The Tidal House",
        "address": "18 Shoreline Lane, Bluewater Cove, PE",
        "details": [
            "Oceanfront rooms with shared breakfast veranda",
            "Parking included for all overnight guests",
            "Five-minute walk to the boardwalk and marina",
        ],
    },
    "travel_notes": [
        "Airport pickup can be arranged for Friday arrivals before 5:00 PM.",
        "Reply with dietary notes in the RSVP form below.",
        "Weekend attire is coastal cocktail with room for comfort.",
    ],
}


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "seashell-dev-key")

    @app.get("/")
    def home() -> str:
        return render_template("index.html", content=SITE_CONTENT)

    @app.post("/rsvp")
    def rsvp() -> Any:
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        party_size = request.form.get("party_size", "1").strip()
        note = request.form.get("note", "").strip()

        if not name or not email:
            flash("Please share both your name and email so we can confirm your plans.", "error")
            return redirect(url_for("home") + "#rsvp")

        flash(
            f"Thanks, {name}. Your RSVP for {party_size} has been received. We will follow up at {email}.",
            "success",
        )

        if note:
            app.logger.info("RSVP note from %s: %s", name, note)

        return redirect(url_for("home") + "#rsvp")

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
