import asyncio
import logging
import sys
import os
import json
import platform
import re
import shutil
import ipaddress
import socket
from collections import deque
from datetime import datetime

TARGETS = ["8.8.8.8", "8.8.4.4", "mkm.net.br", "google.com", "187.102.40.2", "187.102.32.2"]


class TargetState:
    def __init__(self):
        self.fail_streak = 0
        self.success_streak = 0
        self.outage = False
        self.outage_start = None


def _extract_latency_ms(output_text):
    try:
        m = re.search(r"(tempo|time)[=<]?\s*([\d,\.]+)\s*ms", output_text, re.IGNORECASE)
        if m:
            val = m.group(2).replace(",", ".")
            return float(val)
    except Exception:
        pass
    return None


async def ping_once(target, timeout_ms=1000):
    is_win = platform.system().lower().startswith("win")
    is_ip = True
    try:
        ipaddress.ip_address(target)
    except Exception:
        is_ip = False
    if not is_ip:
        timeout_ms = max(timeout_ms, 2000)
    if is_win:
        cmd = ["ping", "-n", "1", "-w", str(timeout_ms), "-4", target]
    else:
        seconds = max(1, timeout_ms // 1000)
        cmd = ["ping", "-c", "1", "-W", str(seconds), target]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, err = await proc.communicate()
        ok = proc.returncode == 0
        text = (out or b"").decode(errors="ignore")
        latency = _extract_latency_ms(text) if ok else None
        return ok, latency
    except Exception:
        return False, None


def _color(s, color_code):
    return f"\x1b[{color_code}m{s}\x1b[0m"


def _level_char(latency_ms):
    if latency_ms is None:
        return "×"
    levels = [20, 50, 100, 200, 400, 800]
    chars = ["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
    for i, th in enumerate(levels):
        if latency_ms <= th:
            return chars[i]
    return chars[-1]


def save_downtime_event(target, start_time, end_time, duration):
    filename = "downtime_events.json"
    events = []
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                events = json.load(f)
        except Exception:
            pass

    new_event = {
        "target": target,
        "start": start_time.strftime("%Y-%m-%d %H:%M:%S"),
        "end": end_time.strftime("%Y-%m-%d %H:%M:%S"),
        "duration_s": duration
    }
    events.append(new_event)

    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(events, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Erro ao salvar evento: {e}")

    generate_html_report(events)


def generate_html_report(events):
    # Sort events by start time descending (most recent first)
    sorted_events = sorted(events, key=lambda x: x["start"], reverse=True)

    cards_html = []
    
    for e in sorted_events:
        try:
            s = datetime.strptime(e["start"], "%Y-%m-%d %H:%M:%S")
            e_time = datetime.strptime(e["end"], "%Y-%m-%d %H:%M:%S")
            
            duration_s = e['duration_s']
            
            # Format duration nicely
            if duration_s >= 60:
                mins = int(duration_s // 60)
                secs = int(duration_s % 60)
                duration_fmt = f"{mins}m {secs}s"
            else:
                duration_fmt = f"{duration_s:.1f}s"
            
            # Formatted strings
            s_str = s.strftime("%d/%m/%Y %H:%M:%S")
            e_str = e_time.strftime("%d/%m/%Y %H:%M:%S")
            date_str = s.strftime("%d/%m/%Y")
            
            # Determine severity color based on duration
            if duration_s < 30:
                severity_class = "severity-low"
                severity_label = "Curta"
            elif duration_s < 120:
                severity_class = "severity-medium"
                severity_label = "Média"
            else:
                severity_class = "severity-high"
                severity_label = "Longa"
            
            card = f'''
            <div class="card {severity_class}">
                <div class="card-header">
                    <span class="target">{e['target']}</span>
                    <span class="severity-badge">{severity_label}</span>
                </div>
                <div class="card-body">
                    <div class="info-row">
                        <span class="label">📅 Data:</span>
                        <span class="value">{date_str}</span>
                    </div>
                    <div class="info-row">
                        <span class="label">🔴 Caiu às:</span>
                        <span class="value">{s.strftime("%H:%M:%S")}</span>
                    </div>
                    <div class="info-row">
                        <span class="label">🟢 Voltou às:</span>
                        <span class="value">{e_time.strftime("%H:%M:%S")}</span>
                    </div>
                    <div class="duration-display">
                        <span class="duration-label">Tempo Offline:</span>
                        <span class="duration-value">{duration_fmt}</span>
                    </div>
                </div>
            </div>
            '''
            cards_html.append(card)
        except Exception:
            continue
    
    cards_content = "\n".join(cards_html)
    total_events = len(sorted_events)
    
    # Aggregate data for charts
    target_stats = {}
    for e in events:
        target = e['target']
        if target not in target_stats:
            target_stats[target] = {'count': 0, 'total_duration': 0}
        target_stats[target]['count'] += 1
        target_stats[target]['total_duration'] += e['duration_s']
    
    # Prepare chart data
    chart_labels = list(target_stats.keys())
    chart_counts = [target_stats[t]['count'] for t in chart_labels]
    chart_durations = [round(target_stats[t]['total_duration'], 1) for t in chart_labels]
    
    # Prepare timeline data (outages by hour with concurrent count)
    from collections import defaultdict
    hourly_outages = defaultdict(lambda: {'targets': set(), 'count': 0})
    for e in events:
        try:
            s = datetime.strptime(e["start"], "%Y-%m-%d %H:%M:%S")
            hour_key = s.strftime("%d/%m %H:00")
            hourly_outages[hour_key]['targets'].add(e['target'])
            hourly_outages[hour_key]['count'] += 1
        except:
            pass
    
    # Sort by datetime and prepare for chart
    sorted_hours = sorted(hourly_outages.keys(), key=lambda x: datetime.strptime(x, "%d/%m %H:00"))
    timeline_labels = sorted_hours
    timeline_counts = [hourly_outages[h]['count'] for h in sorted_hours]
    timeline_targets = [list(hourly_outages[h]['targets']) for h in sorted_hours]
    
    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <title>Relatório de Quedas de Conexão</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    * {{
      box-sizing: border-box;
    }}
    body {{
      font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
      margin: 0;
      padding: 20px;
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      min-height: 100vh;
    }}
    .container {{
      max-width: 1200px;
      margin: 0 auto;
    }}
    h1 {{
      text-align: center;
      color: white;
      text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
      margin-bottom: 10px;
    }}
    h2 {{
      color: white;
      text-shadow: 1px 1px 2px rgba(0,0,0,0.3);
      margin-top: 40px;
      margin-bottom: 20px;
    }}
    .summary {{
      text-align: center;
      color: rgba(255,255,255,0.9);
      margin-bottom: 30px;
      font-size: 1.1em;
    }}
    .charts-container {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
      gap: 20px;
      margin-bottom: 40px;
    }}
    .chart-box {{
      background: white;
      border-radius: 12px;
      padding: 20px;
      box-shadow: 0 4px 15px rgba(0,0,0,0.2);
    }}
    .chart-title {{
      text-align: center;
      font-size: 1.1em;
      font-weight: 600;
      color: #333;
      margin-bottom: 15px;
    }}
    .chart-subtitle {{
      text-align: center;
      font-size: 0.9em;
      color: #666;
      margin: -10px 0 15px 0;
    }}
    .chart-box-full {{
      background: white;
      border-radius: 12px;
      padding: 20px;
      box-shadow: 0 4px 15px rgba(0,0,0,0.2);
      margin-bottom: 30px;
    }}
    .cards-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
      gap: 20px;
    }}
    .card {{
      background: white;
      border-radius: 12px;
      box-shadow: 0 4px 15px rgba(0,0,0,0.2);
      overflow: hidden;
      transition: transform 0.2s ease;
    }}
    .card:hover {{
      transform: translateY(-5px);
    }}
    .card-header {{
      padding: 15px 20px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }}
    .severity-low .card-header {{
      background: linear-gradient(90deg, #28a745, #5cb85c);
    }}
    .severity-medium .card-header {{
      background: linear-gradient(90deg, #ffc107, #ffca2c);
    }}
    .severity-high .card-header {{
      background: linear-gradient(90deg, #dc3545, #e4606d);
    }}
    .target {{
      font-weight: bold;
      color: white;
      font-size: 1.1em;
      text-shadow: 1px 1px 2px rgba(0,0,0,0.3);
    }}
    .severity-badge {{
      background: rgba(255,255,255,0.3);
      color: white;
      padding: 4px 10px;
      border-radius: 20px;
      font-size: 0.8em;
      font-weight: bold;
    }}
    .card-body {{
      padding: 20px;
    }}
    .info-row {{
      display: flex;
      justify-content: space-between;
      padding: 8px 0;
      border-bottom: 1px solid #eee;
    }}
    .info-row:last-of-type {{
      border-bottom: none;
    }}
    .label {{
      color: #666;
      font-weight: 500;
    }}
    .value {{
      color: #333;
      font-weight: 600;
    }}
    .duration-display {{
      margin-top: 15px;
      padding: 15px;
      background: #f8f9fa;
      border-radius: 8px;
      text-align: center;
    }}
    .duration-label {{
      display: block;
      color: #666;
      font-size: 0.9em;
      margin-bottom: 5px;
    }}
    .duration-value {{
      font-size: 1.8em;
      font-weight: bold;
      color: #dc3545;
    }}
    .no-events {{
      text-align: center;
      color: white;
      padding: 50px;
      font-size: 1.2em;
    }}
  </style>
</head>
<body>
  <div class="container">
    <h1>📡 Relatório de Quedas de Conexão</h1>
    <p class="summary">Total de quedas registradas: <strong>{total_events}</strong></p>
    
    <div class="charts-container">
      <div class="chart-box">
        <div class="chart-title">📊 Quantidade de Quedas por Alvo</div>
        <canvas id="countChart"></canvas>
      </div>
      <div class="chart-box">
        <div class="chart-title">⏱️ Tempo Total Offline por Alvo (segundos)</div>
        <canvas id="durationChart"></canvas>
      </div>
    </div>
    
    <div class="chart-box-full">
      <div class="chart-title">📈 Linha do Tempo - Quedas por Hora</div>
      <p class="chart-subtitle">Pontos maiores = mais alvos caíram no mesmo horário (🔴 vermelho = 3+ alvos)</p>
      <canvas id="timelineChart" height="100"></canvas>
    </div>
    
    <h2>📋 Histórico Detalhado</h2>
    <div class="cards-grid">
      {cards_content if cards_content else '<div class="no-events">Nenhuma queda registrada ainda.</div>'}
    </div>
  </div>
  
  <script>
    const labels = {chart_labels};
    const countData = {chart_counts};
    const durationData = {chart_durations};
    
    // Count Chart
    new Chart(document.getElementById('countChart'), {{
      type: 'bar',
      data: {{
        labels: labels,
        datasets: [{{
          label: 'Quedas',
          data: countData,
          backgroundColor: 'rgba(220, 53, 69, 0.7)',
          borderColor: 'rgba(220, 53, 69, 1)',
          borderWidth: 2,
          borderRadius: 8
        }}]
      }},
      options: {{
        responsive: true,
        plugins: {{
          legend: {{ display: false }}
        }},
        scales: {{
          y: {{
            beginAtZero: true,
            ticks: {{ stepSize: 1 }}
          }}
        }}
      }}
    }});
    
    // Duration Chart
    new Chart(document.getElementById('durationChart'), {{
      type: 'bar',
      data: {{
        labels: labels,
        datasets: [{{
          label: 'Segundos Offline',
          data: durationData,
          backgroundColor: 'rgba(102, 126, 234, 0.7)',
          borderColor: 'rgba(102, 126, 234, 1)',
          borderWidth: 2,
          borderRadius: 8
        }}]
      }},
      options: {{
        responsive: true,
        plugins: {{
          legend: {{ display: false }}
        }},
        scales: {{
          y: {{
            beginAtZero: true
          }}
        }}
      }}
    }});
    
    // Timeline Chart with outage markers
    const timelineLabels = {timeline_labels};
    const timelineCounts = {timeline_counts};
    const timelineTargets = {timeline_targets};
    
    // Dynamic point sizes and colors based on concurrent outages
    const pointRadii = timelineCounts.map(count => count >= 3 ? 15 : (count >= 2 ? 10 : 6));
    const pointColors = timelineCounts.map(count => 
      count >= 3 ? 'rgba(220, 53, 69, 1)' : (count >= 2 ? 'rgba(255, 193, 7, 1)' : 'rgba(102, 126, 234, 1)')
    );
    const pointBorderColors = timelineCounts.map(count => 
      count >= 3 ? 'rgba(150, 30, 45, 1)' : (count >= 2 ? 'rgba(200, 150, 0, 1)' : 'rgba(60, 80, 180, 1)')
    );
    
    new Chart(document.getElementById('timelineChart'), {{
      type: 'line',
      data: {{
        labels: timelineLabels,
        datasets: [{{
          label: 'Quedas por Hora',
          data: timelineCounts,
          borderColor: 'rgba(102, 126, 234, 0.5)',
          backgroundColor: 'rgba(102, 126, 234, 0.1)',
          borderWidth: 2,
          fill: true,
          tension: 0.3,
          pointRadius: pointRadii,
          pointBackgroundColor: pointColors,
          pointBorderColor: pointBorderColors,
          pointBorderWidth: 2,
          pointHoverRadius: 18
        }}]
      }},
      options: {{
        responsive: true,
        plugins: {{
          legend: {{ display: false }},
          tooltip: {{
            callbacks: {{
              label: function(context) {{
                const idx = context.dataIndex;
                const count = timelineCounts[idx];
                const targets = timelineTargets[idx];
                return [`Quedas: ${{count}}`, `Alvos: ${{targets.join(', ')}}`];
              }}
            }}
          }}
        }},
        scales: {{
          y: {{
            beginAtZero: true,
            ticks: {{ stepSize: 1 }},
            title: {{
              display: true,
              text: 'Quantidade de Quedas'
            }}
          }},
          x: {{
            title: {{
              display: true,
              text: 'Horário'
            }}
          }}
        }}
      }}
    }});
  </script>
</body>
</html>"""

    try:
        with open("downtime_report.html", "w", encoding="utf-8") as f:
            f.write(html)
    except Exception as e:
        logging.error(f"Erro ao gerar HTML: {e}")


def render_dashboard(histories, states):
    cols = shutil.get_terminal_size(fallback=(100, 24)).columns
    width = max(30, cols - 28)
    print("\x1b[2J\x1b[H", end="")
    print(_color("PING MONITOR", "1;37"))
    print("Legenda: sucesso por latência ▁ rápido → █ lento | falha ×")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"Atualizado: {now}")
    for t in TARGETS:
        hist = list(histories[t])
        if len(hist) < width:
            hist = [None] * (width - len(hist)) + hist
        line = []
        for v in hist[-width:]:
            ch = _level_char(v)
            if v is None:
                line.append(_color(ch, "31"))
            elif v <= 50:
                line.append(_color(ch, "32"))
            elif v <= 200:
                line.append(_color(ch, "33"))
            else:
                line.append(_color(ch, "31"))
        st = states[t]
        status = "OK" if not st.outage else "DOWN"
        status_col = _color(status, "32") if status == "OK" else _color(status, "31")
        print(f"{t:<12} {status_col}  {''.join(line)}")
    print("")
    print("Ctrl+C para sair")


async def monitor(targets, interval_s=1.0, loss_threshold=3, recovery_success_threshold=11):
    states = {t: TargetState() for t in targets}
    cols = shutil.get_terminal_size(fallback=(100, 24)).columns
    width = max(30, cols - 28)
    histories = {t: deque(maxlen=width) for t in targets}
    while True:
        tasks = [ping_once(t) for t in targets]
        results = await asyncio.gather(*tasks)
        now = datetime.now()
        for t, res in zip(targets, results):
            ok, latency = res
            st = states[t]
            if ok:
                if latency is None:
                    latency = 1.0
                st.success_streak += 1
                st.fail_streak = 0
                histories[t].append(latency)
                if st.outage and st.success_streak >= recovery_success_threshold:
                    duration = (now - st.outage_start).total_seconds()
                    logging.info(
                        "Voltou: %s às %s, tempo até voltar: %.1fs",
                        t,
                        now.strftime("%Y-%m-%d %H:%M:%S"),
                        duration,
                    )
                    save_downtime_event(t, st.outage_start, now, duration)
                    st.outage = False
                    st.outage_start = None
                    st.success_streak = 0
            else:
                st.fail_streak += 1
                st.success_streak = 0
                histories[t].append(None)
                if not st.outage and st.fail_streak >= loss_threshold:
                    st.outage = True
                    st.outage_start = now
                    logging.warning(
                        "Indisponível: %s às %s (perdas consecutivas: %d)",
                        t,
                        now.strftime("%Y-%m-%d %H:%M:%S"),
                        st.fail_streak,
                    )
        render_dashboard(histories, states)
        await asyncio.sleep(interval_s)


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler("ping_monitor.log", encoding="utf-8")],
    )


def main():
    setup_logging()
    try:
        asyncio.run(monitor(TARGETS))
    except KeyboardInterrupt:
        logging.info("Encerrado")


if __name__ == "__main__":
    main()

