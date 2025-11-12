import asyncio
from playwright.async_api import async_playwright
import pandas as pd
from datetime import datetime
from flask import Flask, request, jsonify
import os

app = Flask(__name__)

# ========================================
# FUNCIÓN DE SCRAPING (misma que tenías)
# ========================================

# Usaremos un set para evitar duplicados si la paginación se solapa
scraped_review_titles = set()
reviews_data = []

async def safe_get_text(card, selector, default=""):
    """Función helper para extraer texto de forma segura."""
    try:
        locator = card.locator(selector).first
        if await locator.count() > 0:
            return (await locator.inner_text()).strip()
    except:
        pass
    return default

async def extract_reviews_from_page(page):
    """Extrae todas las reseñas de la página actualmente visible."""
    global reviews_data, scraped_review_titles

    print("   🔍 Buscando tarjetas de reseñas en la página actual...")
    await page.wait_for_selector('div[data-testid="review-card"]', timeout=15000)
    review_cards = await page.locator('div[data-testid="review-card"]').all()
    print(f"   📄 Encontradas {len(review_cards)} reseñas.")

    new_reviews_added = 0
    for card in review_cards:
        try:
            review_title = await safe_get_text(card, 'h4[data-testid="review-title"]')

            # Evitar duplicados
            if review_title and review_title in scraped_review_titles:
                continue

            scraped_review_titles.add(review_title)

            # --- Autor y Estancia ---
            user_name = await safe_get_text(card, 'div.b08850ce41')
            user_country = await safe_get_text(card, 'span.d838fb5f41')
            traveler_type = await safe_get_text(card, 'span[data-testid="review-traveler-type"]')
            review_date = await safe_get_text(card, 'span[data-testid="review-date"]')

            # --- Rating ---
            rating_raw = await safe_get_text(card, 'div[data-testid="review-score"] div.bc946a29db')
            rating = rating_raw.replace("Puntuación: ", "").strip()

            # --- Comentarios Positivos (+) ---
            liked_text_full = await safe_get_text(card, 'div[data-testid="review-positive-text"]')
            liked_text = liked_text_full.splitlines()[-1].strip() if liked_text_full.splitlines() else ""

            # --- Comentarios Negativos (-) ---
            disliked_text_full = await safe_get_text(card, 'div[data-testid="review-negative-text"]')
            disliked_text = disliked_text_full.splitlines()[-1].strip() if disliked_text_full.splitlines() else ""

            reviews_data.append({
                "Rating": rating,
                "Review Title": review_title,
                "Liked Text": liked_text,
                "Disliked Text": disliked_text,
                "Review Date": review_date,
                "Traveler Type": traveler_type,
                "User Name": user_name,
                "User Country": user_country,
                "Scraped Timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            })
            new_reviews_added += 1

        except Exception as e:
            print(f"   ❌ Error en una reseña: {str(e)[:100]}...")

    print(f"   ✨ {new_reviews_added} reseñas nuevas añadidas. Total: {len(reviews_data)}")


async def scrape_booking_reviews(url, filter_option="default", max_pages=2):
    """
    Scraper de Booking.com con filtros y paginación.
    """
    global reviews_data, scraped_review_titles
    reviews_data = []
    scraped_review_titles = set()

    async with async_playwright() as p:
        print("🌐 Iniciando navegador...")
        browser = await p.chromium.launch(
            headless=True,
            args=['--disable-gpu', '--disable-dev-shm-usage', '--no-sandbox']
        )
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
            locale='es-ES' # Pedir la página en español
        )
        page = await context.new_page()

        try:
            print(f"➡️ Navegando a: {url}")
            await page.goto(url, timeout=90000, wait_until='networkidle')

            # --- 1. Manejar Cookies ---
            try:
                cookie_btn = page.locator('#onetrust-accept-btn-handler')
                if await cookie_btn.is_visible(timeout=5000):
                    print("🍪 Aceptando cookies...")
                    await cookie_btn.click()
                    await page.wait_for_timeout(1000)
            except:
                print("ℹ️ No se encontró el banner de cookies.")

            # --- 2. Ir a la sección de Reseñas ---
            try:
                await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
                read_all_btn = page.locator('button[data-testid="fr-read-all-reviews"]')
                if await read_all_btn.is_visible(timeout=5000):
                    print("✅ Clic en 'Leer todos los comentarios'...")
                    await read_all_btn.click()
                    await page.wait_for_load_state('networkidle', timeout=15000)
                else:
                    print("ℹ️ Asumiendo que ya estamos en la página de reseñas.")
            except Exception as e:
                print(f"⚠️ No se pudo hacer clic en 'Leer todos' (puede que no exista): {e}")

            # --- 3. Aplicar Filtro de Ordenamiento ---
            if filter_option != "default":
                try:
                    print(f"🔧 Aplicando filtro: {filter_option}")
                    await page.locator('button[data-testid="sorters-dropdown-trigger"]').click()
                    await page.wait_for_timeout(500)
                    await page.locator(f'button[data-testid="sorters-dropdown-option-{filter_option}"]').click()
                    print("⏳ Esperando que el filtro se aplique...")
                    await page.wait_for_load_state('networkidle', timeout=15000)
                    print("✅ Filtro aplicado.")
                except Exception as e:
                    print(f"⚠️ No se pudo aplicar el filtro: {e}")

            # --- 4. Loop de Paginación ---
            for page_num in range(1, max_pages + 1):
                print("\n" + "="*30)
                print(f"📄 Extrayendo página {page_num} de {max_pages}...")

                await extract_reviews_from_page(page)

                if page_num < max_pages:
                    # Ir a la página siguiente
                    try:
                        next_btn = page.locator('button[aria-label="Página siguiente"]')
                        if await next_btn.count() == 0 or not await next_btn.is_enabled():
                            print("🏁 No hay más páginas. Terminando.")
                            break

                        print("⏩ Clic en 'Página siguiente'...")
                        await next_btn.click()
                        await page.wait_for_load_state('networkidle', timeout=15000)
                    except Exception as e:
                        print(f"❌ Error al paginar: {e}")
                        break

        except Exception as e:
            print(f"❌ Error fatal durante el scraping: {e}")

        await browser.close()
        print(f"\n✅ Scraping completado. Total de reseñas únicas: {len(reviews_data)}")
        return pd.DataFrame(reviews_data)

# ========================================
# FLASK API
# ========================================

@app.route('/scrape', methods=['POST'])
def handle_scrape():
    data = request.json
    hotel_name = data.get('hotel_name')

    if not hotel_name:
        return jsonify({"error": "No se proporcionó nombre del hotel"}), 400

    # Aquí puedes construir la URL de Booking para el hotel
    # Por ejemplo, si tienes una forma de buscar el hotel en Booking
    # En este ejemplo, usaremos una URL fija como placeholder
    # Deberás implementar la lógica de búsqueda real aquí
    url = f"https://www.booking.com/hotel/pe/{hotel_name.replace(' ', '-').lower()}.es.html"

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        df = loop.run_until_complete(scrape_booking_reviews(url, max_pages=2))
        results = df.to_dict(orient='records')
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/', methods=['GET'])
def home():
    return jsonify({"message": "API de scraping de hoteles en línea. Usa POST /scrape con {hotel_name}"})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)