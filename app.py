import os
from flask import Flask, jsonify, render_template, request
import sqlite3

app = Flask(__name__)

# --- Database Setup ---
DATA_DIR = "data"
DB_FILE = os.path.join(DATA_DIR, "wallet.db")

def get_db_connection():
    """Establishes a connection to the SQLite database."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initializes the database with the required tables and default banknotes."""
    # Ensure the data directory exists.
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(DB_FILE):
        return # Avoid re-initializing

    print("Initializing database...")
    conn = get_db_connection()
    cursor = conn.cursor()

    # Banknotes table
    cursor.execute('''
        CREATE TABLE banknotes (
            id INTEGER PRIMARY KEY,
            value INTEGER UNIQUE NOT NULL,
            name TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0
        )
    ''')

    # Transactions table
    cursor.execute('''
        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            note TEXT,
            amount INTEGER NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Transaction details table to log banknote movements
    cursor.execute('''
        CREATE TABLE transaction_details (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id INTEGER NOT NULL,
            banknote_value INTEGER NOT NULL,
            count_change INTEGER NOT NULL, -- positive for received, negative for paid
            FOREIGN KEY (transaction_id) REFERENCES transactions (id)
        )
    ''')
    
    # Pre-populate with Indonesian Rupiah denominations
    banknotes = [
        (100000, 'Rp 100,000'), (50000, 'Rp 50,000'), (20000, 'Rp 20,000'),
        (10000, 'Rp 10,000'), (5000, 'Rp 5,000'), (2000, 'Rp 2,000'), (1000, 'Rp 1,000')
    ]
    cursor.executemany('INSERT INTO banknotes (value, name) VALUES (?, ?)', banknotes)
    
    conn.commit()
    conn.close()
    print("Database initialized.")

# --- API Endpoints ---

@app.route('/')
def index():
    """Renders the main HTML page."""
    return render_template('index.html')

@app.route('/api/wallet', methods=['GET'])
def get_wallet_status():
    """Retrieves the current count of each banknote and the total cash."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT value, name, count FROM banknotes ORDER BY value DESC')
    banknotes = [dict(row) for row in cursor.fetchall()]
    
    total_cash = sum(note['value'] * note['count'] for note in banknotes)
    
    conn.close()
    return jsonify({
        'banknotes': banknotes,
        'total_cash': total_cash
    })

@app.route('/api/transaction', methods=['POST'])
def create_transaction():
    """Handles a new transaction, updating banknote counts."""
    data = request.json
    note = data.get('note', 'Transaction')
    amount = data.get('amount', 0)
    paid_with = data.get('paid_with', {})
    change_received = data.get('change_received', {})

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # 1. Deduct notes paid with
        for value_str, count in paid_with.items():
            if count > 0:
                cursor.execute(
                    'UPDATE banknotes SET count = count - ? WHERE value = ? AND count >= ?',
                    (count, int(value_str), count)
                )
                if cursor.rowcount == 0:
                    raise ValueError(f"Not enough {value_str} notes in wallet.")

        # 2. Add notes received as change
        for value_str, count in change_received.items():
            if count > 0:
                cursor.execute(
                    'UPDATE banknotes SET count = count + ? WHERE value = ?',
                    (count, int(value_str))
                )

        # 3. Log the transaction
        cursor.execute(
            'INSERT INTO transactions (note, amount) VALUES (?, ?)',
            (note, amount)
        )
        transaction_id = cursor.lastrowid

        # 4. Log the banknote movements for this transaction
        details_to_log = []
        for value_str, count in paid_with.items():
            if count > 0:
                details_to_log.append((transaction_id, int(value_str), -count))
        for value_str, count in change_received.items():
            if count > 0:
                details_to_log.append((transaction_id, int(value_str), count))
        
        cursor.executemany(
            'INSERT INTO transaction_details (transaction_id, banknote_value, count_change) VALUES (?, ?, ?)',
            details_to_log
        )

        conn.commit()
    except (ValueError, sqlite3.Error) as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 400
    finally:
        conn.close()
        
    return jsonify({'message': 'Transaction successful'}), 201

@app.route('/api/wallet/adjust', methods=['POST'])
def adjust_wallet():
    """Directly updates the counts of each banknote in the wallet."""
    data = request.json
    adjustments = data.get('adjustments', {})

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        for value_str, count in adjustments.items():
            cursor.execute(
                'UPDATE banknotes SET count = ? WHERE value = ?',
                (int(count), int(value_str))
            )
        conn.commit()
    except sqlite3.Error as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 400
    finally:
        conn.close()

    return jsonify({'message': 'Wallet adjusted successfully'}), 200

@app.route('/api/history', methods=['GET'])
def get_history():
    """Retrieves the last 20 transactions."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id, note, amount, timestamp FROM transactions ORDER BY timestamp DESC LIMIT 20')
    history = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(history)

@app.route('/api/transaction/<int:transaction_id>', methods=['DELETE'])
def delete_transaction(transaction_id):
    """Deletes a transaction and reverts the banknote counts."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # 1. Get the banknote movements for the transaction
        cursor.execute(
            'SELECT banknote_value, count_change FROM transaction_details WHERE transaction_id = ?',
            (transaction_id,)
        )
        details = cursor.fetchall()
        if not details:
            return jsonify({'error': 'Transaction not found or has no details to revert.'}), 404

        # 2. Revert the banknote counts
        for detail in details:
            # The change is reversed: if we paid (-), we add back (+). If we received (+), we subtract (-).
            cursor.execute(
                'UPDATE banknotes SET count = count - ? WHERE value = ?',
                (detail['count_change'], detail['banknote_value'])
            )

        # 3. Delete the details and the transaction itself
        cursor.execute('DELETE FROM transaction_details WHERE transaction_id = ?', (transaction_id,))
        cursor.execute('DELETE FROM transactions WHERE id = ?', (transaction_id,))

        conn.commit()
    except sqlite3.Error as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

    return jsonify({'message': 'Transaction deleted successfully'}), 200

# Initialize the database when the app starts
init_db()
