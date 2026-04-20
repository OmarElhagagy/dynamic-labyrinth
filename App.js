const e = React.createElement;

function Card({ title, children }) {
  return e("div", { style: {
      background: "white",
      padding: 15,
      margin: 15,
      borderRadius: 8,
      boxShadow: "0 0 5px rgba(0,0,0,0.15)"
  }}, 
    e("h3", null, title),
    children
  );
}

function Dashboard() {
  const [health, setHealth] = React.useState({});
  const [alerts, setAlerts] = React.useState([]);
  const [sessions, setSessions] = React.useState([]);

  React.useEffect(() => {
    setInterval(() => {
      setHealth({
        cpu: (10 + Math.random()*40).toFixed(1),
        mem: (20 + Math.random()*50).toFixed(1),
        eps: (50 + Math.random()*200).toFixed(0)
      });

      setAlerts([
        "[HIGH] Priv-Esc attempt",
        "[MED] Suspicious SSH brute force"
      ]);

      setSessions([
        { id: "sess-101", ip: "203.0.113." + Math.floor(Math.random()*255), entry: "SSH", status:"active" },
        { id: "sess-102", ip: "203.0.113." + Math.floor(Math.random()*255), entry: "HTTP", status:"active" }
      ]);
    }, 1500);
  }, []);

  return e("div", null,
    e("div", { style: { background:"#222", color:"white", padding:15, fontSize:22 }}, 
      "Security Dashboard Demo (React)"
    ),

    e("div", { style:{ display:"flex" }},
      e(Card, { title:"System Health" },
        `CPU: ${health.cpu}%`, e("br"),
        `Memory: ${health.mem}%`, e("br"),
        `EPS: ${health.eps}`
      ),
      e(Card, { title:"Alerts" },
        alerts.map(a => e("div", { style:{color:"red"} }, a))
      )
    ),

    e(Card, { title:"Active Sessions" },
      e("table", { style:{ width:"100%", borderCollapse:"collapse" }},
        e("tr", null,
          e("th", null, "ID"),
          e("th", null, "IP"),
          e("th", null, "Entry"),
          e("th", null, "Status")
        ),
        sessions.map(s =>
          e("tr", null,
            e("td", null, s.id),
            e("td", null, s.ip),
            e("td", null, s.entry),
            e("td", null, s.status)
          )
        )
      )
    )
  );
}

export default Dashboard;
