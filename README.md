# Cash Wallet Tracker

A simple, self-hosted web application to track the cash in your physical wallet. This application is built with Flask and SQLite for the backend, and uses Bootstrap and jQuery for a clean and responsive user interface.

## Features

- **Track Banknotes:** Keep a digital record of the quantity of each banknote denomination you have in your wallet.
- **Total Cash View:** Get an at-a-glance view of the total amount of cash in your wallet.
- **Transaction Logging:** Record transactions, including a note, the exact banknotes used for payment, and the change received.
- **Transaction History:** View a detailed history of all your transactions.
- **Filter and Sort:** Filter transactions by period (All Time, Today, This Month) and sort them by date or amount.
- **Wallet Adjustment:** Easily adjust the banknote counts in your wallet to match the physical cash you have on hand.
- **Responsive UI:** A clean and modern user interface that works on both desktop and mobile devices.

## Technologies Used

- **Backend:**
  - Python
  - Flask
  - SQLite
- **Frontend:**
  - HTML
  - CSS (Bootstrap 5)
  - JavaScript (jQuery)

## Setup and Installation

1.  **Clone the repository:**
    ```bash
    git clone <your-repository-url>
    cd cash-tracker
    ```

2.  **Create a virtual environment and activate it:**
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```

3.  **Install the required dependencies:**
    ```bash
    pip install Flask
    ```

## Usage

1.  **Run the Flask application:**
    ```bash
    flask run
    ```

2.  **Open your web browser** and navigate to `http://127.0.0.1:5000`.

3.  The application will automatically initialize the database in a `data/` directory upon the first run.

4.  Start tracking your cash!
