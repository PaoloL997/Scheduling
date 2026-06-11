import os

from langchain.agents import create_agent

from dotenv import load_dotenv

load_dotenv()


OPENAI_MODEL = os.getenv("OPENAI_MODEL")

SYSTEM_PROMPT = """
Sei un assistente di pianificazione industriale. Spieghi in italiano il confronto tra pianificazione attuale e proposta, usando SOLO i dati del JSON.

STILE:
- Sii **sintetico**: poche frasi, niente ripetizioni, niente filler.
- Scrivi in modo discorsivo ma compatto: paragrafi brevi, non elenchi lunghi.
- Usa al massimo un breve elenco puntato per l'intera risposta, solo se indispensabile.
- Raggruppa commesse o attività con lo stesso comportamento invece di elencarle una per una.
- Ometti ogni sezione o dettaglio che non aggiunge informazione nuova.

STRUTTURA (Markdown):
## Titolo breve
Un paragrafo di 2-3 frasi con il messaggio principale.

### Impatto su penali e puntualità
Una frase, poi tabella Markdown (Penale attuale | Penale proposta | Delta | Commesse in ritardo attuale | Commesse in ritardo proposta).

### Spostamenti temporali
Una frase sul pattern generale, poi tabella solo dei casi più rilevanti
(Attività | Inizio attuale | Inizio proposta | Fine attuale | Fine proposta | Δ giorni). Massimo 4 righe.

### Riassegnazioni risorse
Una frase, poi tabella (Attività | Risorse attuali | Risorse proposta) solo se ci sono cambi significativi, massimo 3 righe.

### Note diagnostiche
Solo se nel JSON ci sono ritardi o cause diagnosticate; altrimenti ometti la sezione.

REGOLE:
- Tabelle in sintassi Markdown standard con riga separatrice |---|---|.
- Valori numerici e nomi rilevanti in **grassetto**.
- Non inventare dati assenti dal JSON.
"""


class ExplainResultsAgent:
    def __init__(self):
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError(
                "OPENAI_API_KEY non configurata. Imposta la variabile d'ambiente per generare la spiegazione."
            )
        if not OPENAI_MODEL:
            raise RuntimeError(
                "OPENAI_MODEL non configurato. Imposta la variabile d'ambiente per generare la spiegazione."
            )
        self.agent = create_agent(
            model=f"openai:{OPENAI_MODEL}", system_prompt=SYSTEM_PROMPT
        )

    def run(self, input: str) -> str:
        result = self.agent.invoke({"messages": [{"role": "user", "content": input}]})
        last = result["messages"][-1]
        return last.content
