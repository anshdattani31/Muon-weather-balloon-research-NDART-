# Suggested learning order

The complete runnable foundation is present, but you can learn it one section at a time.

1. **Run the default simulation.** Start `main.py`, select CSV and click Start. Stop it and inspect the session folder. The sensor and GNSS values are invented placeholders.
2. **Change storage without editing code.** Select JSONL, start a new run, and compare the records with CSV. The full record is preserved in both.
3. **Read `config.py`.** Each field definition has a tab, label, default and optional list of dropdown choices. `validate()` checks the selected profile.
4. **Read `Simulator` and `normalize()` in `adapters.py`.** Follow a dictionary from a simulated measurement to a normalized record. `raw` holds the original fields.
5. **Read `RecordStore` in `storage.py`.** Follow the same record through the CSV, JSONL and SQLite alternatives.
6. **Read `fragment()` and `Reassembler` in `protocol.py`.** A record becomes JSON bytes, then numbered fragments, then the original JSON object again.
7. **Read `Engine._collect()`, `_transmit()` and `_receive()`.** These connect the independently replaceable pieces. Try simulated packet loss and inspect retry counts.
8. **Read `gui.py`.** A widget modifies a setting; Start takes a validated snapshot. A worker thread runs acquisition while Tkinter updates the window.
9. **Read the hardware contract.** Only after receiving real sample output should the simulator be replaced with the actual device parser.

You can ask about a particular method or line before moving to the next section. A dropdown selects an implemented behavior; an entirely new hardware interface needs a new adapter, rather than arbitrary code pasted into settings.
