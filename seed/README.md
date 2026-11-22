# Seed data

`products.csv` is a **draft assortment template** created because no spreadsheet
existed yet. It lists typical product types only. Prices, VAT rates, suppliers,
min/max stock and shelf life are intentionally empty, and every product is
`active=false`: an inactive product can never be ordered or priced by the agent.

The owner completes the rows (or replaces them via
`make import-assortment XLSX=P4_ducan_model.xlsx`), sets `active=true`, and runs
`make seed`. A product can only be active with a VAT rate, purchase price and
retail price.

`suppliers.csv` is empty: supplier terms are unknown and are not guessed.
`supplier_id` in `products.csv` refers to `id` in `suppliers.csv`.
`delivery_days` uses ISO weekday numbers, e.g. `2,5` = Tuesday and Friday.
