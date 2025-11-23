# ScrapeXi

**AI-Powered Web Scraping API**

ScrapeXi is a self-hosted, API-first web scraper powered by Google Gemini 2.0 Flash. It allows you to extract structured data from any website using natural language queries, bypassing complex selectors and anti-bot measures.

## Features

*   **Natural Language Queries**: Define what you want (e.g., `{ products[] { name, price } }`) and let AI do the rest.
*   **Stealth Mode**: Built-in evasion techniques to scrape protected sites.
*   **Pagination**: Automatically traverse "Next" buttons to scrape multiple pages.
*   **Authentication**: Support for both credential-based login and session cookie reuse.
*   **API & Dashboard**: Includes a FastAPI backend and a modern dashboard for testing and template management.

## Getting Started

1.  **Clone the repository**:
    ```bash
    git clone https://github.com/CrewXI/scrapexi.git
    ```

2.  **Install Dependencies**:
    ```bash
    npm install
    pip install -r requirements.txt
    playwright install chromium
    ```

3.  **Run Locally**:
    ```bash
    npm run dev
    ```

4.  **Environment Variables**:
    Create a `.env` or `env.local` file with your keys:
    *   `GOOGLE_API_KEY`
    *   `SUPABASE_URL`
    *   `SUPABASE_ANON_KEY`
    *   `STRIPE_SECRET_KEY`

## Deployment

This project is optimized for **Vercel**. Simply import the repository, and the `vercel.json` configuration will handle the rest.
test
