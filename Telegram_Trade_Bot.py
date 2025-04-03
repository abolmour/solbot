from telethon import TelegramClient, events
import re
import time
import requests
import base64
import json
import logging
import threading
from solders.keypair import Keypair
from solana.rpc.api import Client
from solana.rpc.types import TxOpts
from solders.transaction import Transaction
from solders.publickey import PublicKey
from flask import Flask, request, jsonify, render_template_string, redirect
import plotly
import plotly.graph_objs as go
import uuid

api_id = 'YOUR_TELEGRAM_API_ID'
api_hash = 'YOUR_TELEGRAM_API_HASH'
channel_username = 'SoEarlyTrending'

AUTH_PASSWORD = 'admin123'

SOLANA_RPC_URL = 'https://api.mainnet-beta.solana.com'
JUPITER_SWAP_API = 'https://quote-api.jup.ag/v6/swap'
JUPITER_QUOTE_API = 'https://quote-api.jup.ag/v6/quote'
JUPITER_PRICE_API = 'https://price.jup.ag/v4/price'
JUPITER_TOKEN_LIST = 'https://cache.jup.ag/tokens'

wallet = Keypair.from_secret_key(base64.b64decode("YOUR_BASE64_SECRET_KEY=="))
solana_client = Client(SOLANA_RPC_URL)

logging.basicConfig(filename='trade_log.txt', level=logging.INFO, format='%(asctime)s - %(message)s')

client = TelegramClient('bot_session', api_id, api_hash)

app = Flask(__name__)
trade_history = []

def extract_token(message):
    match = re.search(r'\$(\w+)', message)
    return match.group(1).upper() if match else None

def resolve_token_mint(symbol):
    try:
        response = requests.get(JUPITER_TOKEN_LIST)
        if response.ok:
            tokens = response.json()
            for token in tokens:
                if token["symbol"].upper() == symbol:
                    return token["address"]
    except:
        pass
    return None

def get_token_price(mint):
    try:
        response = requests.get(f"{JUPITER_PRICE_API}?ids={mint}")
        if response.ok:
            data = response.json()
            return data["data"][mint]["price"]
    except:
        pass
    return None

def get_jupiter_quote(input_mint, output_mint, amount):
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": str(amount),
        "slippageBps": 100,
        "onlyDirectRoutes": False
    }
    response = requests.get(JUPITER_QUOTE_API, params=params)
    return response.json()["data"][0] if response.ok and response.json().get("data") else None

def get_jupiter_swap(quote):
    body = {
        "route": quote,
        "userPublicKey": str(wallet.public_key),
        "wrapUnwrapSOL": True,
        "feeAccount": None
    }
    headers = {"Content-Type": "application/json"}
    response = requests.post(JUPITER_SWAP_API, data=json.dumps(body), headers=headers)
    return response.json() if response.ok else None

def send_signed_tx(base64_tx):
    try:
        swap_tx_decoded = base64.b64decode(base64_tx)
        txn = Transaction.deserialize(swap_tx_decoded)
        txn.sign(wallet)
        res = solana_client.send_transaction(txn, wallet, opts=TxOpts(skip_confirmation=False))
        return res
    except Exception as e:
        print(f"[TX ERROR] {e}")
        return None

def get_wallet_balance():
    return solana_client.get_balance(wallet.public_key)['result']['value'] / 1e9

def buy_token(symbol, amount):
    print(f"[BUY] Trying to buy token ${symbol} on Jupiter")
    sol_mint = "So11111111111111111111111111111111111111112"
    token_mint = resolve_token_mint(symbol)
    if not token_mint:
        print(f"[ERROR] Couldn't resolve token mint for {symbol}")
        return
    lamports = int(amount * 1e9)
    quote = get_jupiter_quote(sol_mint, token_mint, lamports)
    if not quote:
        print("[ERROR] Failed to get Jupiter quote")
        return
    swap_tx = get_jupiter_swap(quote)
    if not swap_tx or "swapTransaction" not in swap_tx:
        print("[ERROR] Failed to get Jupiter swap transaction")
        return
    result = send_signed_tx(swap_tx["swapTransaction"])
    logging.info(f"BUY: {symbol} | TX: {result}")
    trade_history.append({"id": str(uuid.uuid4()), "type": "BUY", "symbol": symbol, "tx": result})
    print(f"[SUCCESS] Sent buy tx: {result}")

def sell_token(symbol, token_mint, percent):
    print(f"[SELL {percent}%] Selling {percent}% of {symbol}")
    sol_mint = "So11111111111111111111111111111111111111112"
    quote = get_jupiter_quote(token_mint, sol_mint, 10000000)
    if not quote:
        print("[ERROR] Failed to get Jupiter quote for selling")
        return
    swap_tx = get_jupiter_swap(quote)
    if not swap_tx or "swapTransaction" not in swap_tx:
        print("[ERROR] Failed to get Jupiter swap transaction for selling")
        return
    result = send_signed_tx(swap_tx["swapTransaction"])
    logging.info(f"SELL {percent}%: {symbol} | TX: {result}")
    trade_history.append({"id": str(uuid.uuid4()), "type": f"SELL {percent}%", "symbol": symbol, "tx": result})
    print(f"[SUCCESS] Sent sell tx: {result}")

def generate_chart():
    symbols = [t['symbol'] for t in trade_history]
    values = list(range(1, len(symbols)+1))
    types = [t['type'] for t in trade_history]
    trace = go.Scatter(x=values, y=symbols, mode='markers+lines', text=types)
    fig = go.Figure(data=[trace])
    return plotly.io.to_html(fig, full_html=False)

@app.route('/')
def dashboard():
    if request.args.get("auth") != AUTH_PASSWORD:
        return "<h3>Access Denied. Append ?auth=admin123 to URL.</h3>"
    html = """
    <html><head><title>Trade Interface</title></head>
    <body>
    <h2>Wallet Balance: {{ balance }} SOL</h2>
    <form method='post' action='/buy'>
        <input name='symbol' placeholder='Token Symbol (e.g. DOGE)' required>
        <input name='amount' placeholder='Amount in SOL (e.g. 0.01)' required>
        <input type='hidden' name='auth' value='{{ auth }}'>
        <button type='submit'>Buy</button>
    </form>
    <form method='post' action='/sell'>
        <input name='symbol' placeholder='Token Symbol (e.g. DOGE)' required>
        <input name='percent' placeholder='Percent (e.g. 50)' required>
        <input type='hidden' name='auth' value='{{ auth }}'>
        <button type='submit'>Sell</button>
    </form>
    <h3>Trade History:</h3>
    <ul>
        {% for trade in trades %}
        <li>{{ trade['type'] }} {{ trade['symbol'] }} - TX: {{ trade['tx'] }}</li>
        {% endfor %}
    </ul>
    <h3>Trade Chart:</h3>
    {{ chart|safe }}
    </body></html>"""
    return render_template_string(html, trades=trade_history[-20:], balance=get_wallet_balance(), auth=AUTH_PASSWORD, chart=generate_chart())

@app.route('/buy', methods=['POST'])
def manual_buy():
    if request.form.get('auth') != AUTH_PASSWORD:
        return redirect('/')
    symbol = request.form['symbol'].upper()
    amount = float(request.form['amount'])
    buy_token(symbol, amount)
    return redirect(f"/?auth={AUTH_PASSWORD}")

@app.route('/sell', methods=['POST'])
def manual_sell():
    if request.form.get('auth') != AUTH_PASSWORD:
        return redirect('/')
    symbol = request.form['symbol'].upper()
    percent = int(request.form['percent'])
    token_mint = resolve_token_mint(symbol)
    if token_mint:
        sell_token(symbol, token_mint, percent)
    return redirect(f"/?auth={AUTH_PASSWORD}")

@client.on(events.NewMessage(chats=channel_username))
async def handler(event):
    message = event.message.message
    if message and "$" in message and "Entry Signal" in message:
        token = extract_token(message)
        if token:
            print(f"[CALL DETECTED] Token: {token}")
            logging.info(f"CALL DETECTED: {token} | Balance: {get_wallet_balance()} SOL")
            buy_token(token, 0.01)

def start_flask():
    app.run(host='0.0.0.0', port=5000)

def start_telegram():
    client.start()
    client.run_until_disconnected()

threading.Thread(target=start_flask).start()
print("Starting Telegram bot...")
start_telegram()
