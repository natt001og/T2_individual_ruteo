const map = L.map('map', {preferCanvas:true}).setView([-36.5,-71.5], 4);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 18, attribution: '&copy; OpenStreetMap contributors'
}).addTo(map);
const renderer = L.canvas({padding:0.5});
const riskColors = {ALTO:'#d73027', MEDIO:'#fc8d59', BAJO:'#1a9850', SIN_ESTIMACION:'#8b95a3'};
const nodeColors = {
  centro_generador:'#6a3d9a', ixp_pit_modelado:'#1f78b4',
  aterrizaje_submarino_modelado:'#ff7f00', nodo_troncal:'#e31a1c',
  terminal_receptor:'#33a02c'
};
const edgeLayer = L.geoJSON(null, {
  renderer,
  style: f => ({
    color: riskColors[f.properties.nivel_riesgo] || riskColors.SIN_ESTIMACION,
    weight: f.properties.nivel_riesgo === 'ALTO' ? 2.5 : 1.6,
    opacity: 0.78,
    dashArray: f.properties.es_sintetico ? '6 5' : null
  }),
  onEachFeature: (f, layer) => {
    const p=f.properties;
    const prob=p.prob_falla==null?'Sin estimación':(100*p.prob_falla).toFixed(2)+' %';
    const pga=p.pga_maxima==null?'Sin dato':Number(p.pga_maxima).toFixed(4)+' g';
    layer.bindPopup(`<div class="popup-title">Enlace ${p.id_arista}</div><b>Origen:</b> ${p.origen}<br><b>Tipo:</b> ${p.tipo_enlace}<br><b>Infraestructura:</b> ${p.es_sintetico?'Sintética / hipotética':'Detectada'}<br><b>PGA máxima:</b> ${pga}<br><b>Probabilidad de falla:</b> ${prob}<br><b>Riesgo:</b> ${p.nivel_riesgo}`);
  }
});
const nodeLayer = L.geoJSON(null, {
  pointToLayer: (f, latlng) => L.circleMarker(latlng, {
    renderer, radius:6, color:'#fff', weight:1.2,
    fillColor:nodeColors[f.properties.tipo_instalacion]||'#555', fillOpacity:0.95
  }),
  onEachFeature: (f, layer) => {
    const p=f.properties;
    layer.bindPopup(`<div class="popup-title">Nodo ${p.id_vertice}</div><b>Rol:</b> ${p.rol}<br><b>Instalación modelada:</b> ${p.tipo_instalacion}<br><b>Grado:</b> ${p.grado}<br><b>Componente:</b> ${p.id_componente}`);
  }
});
const epicenterLayer = L.geoJSON(null, {
  pointToLayer: (f, latlng) => L.marker(latlng, {icon:L.divIcon({className:'',html:'<div style="font-size:25px;color:#b30000;text-shadow:0 0 3px white">✹</div>',iconSize:[26,26],iconAnchor:[13,13]})}),
  onEachFeature: (f, layer) => layer.bindPopup(`<b>Epicentro: ${f.properties.evento}</b><br>Magnitud: ${f.properties.magnitud}<br>Profundidad: ${Number(f.properties.profundidad_km).toFixed(1)} km`)
});

function fmt(n){return Number(n||0).toLocaleString('es-CL')}
function renderAttr(items){
  const labels={pre_terremoto:'Antes del terremoto',post_optimista:'Posterior · optimista',post_conservador:'Posterior · conservador'};
  document.getElementById('attr-results').innerHTML=items.map(x=>`<div class="attr-card"><b>${labels[x.escenario]||x.escenario}</b><strong>${(x.attr*100).toFixed(2)} %</strong><small>IC 95 %: ${(x.ic95_inferior*100).toFixed(2)}–${(x.ic95_superior*100).toFixed(2)} %</small></div>`).join('')||'No hay resultados ATTR guardados.';
}
function addLegend(){
  const legend=L.control({position:'bottomright'});
  legend.onAdd=()=>{const d=L.DomUtil.create('div','legend');d.innerHTML='<b>Probabilidad de falla</b><br><i style="background:#d73027"></i>Riesgo alto<br><i style="background:#fc8d59"></i>Riesgo medio<br><i style="background:#1a9850"></i>Riesgo bajo<br><i style="background:#8b95a3"></i>Sin estimación<br><hr style="border:0;border-top:1px solid #ddd"><b>Tipo de enlace</b><br>Continua: geometría observada<br>Discontinua: enlace sintético';return d};legend.addTo(map);
}
async function load(){
  try{
    const [edges,nodes,epi,summary]=await Promise.all([
      fetch('/api/edges').then(r=>r.json()),fetch('/api/nodes').then(r=>r.json()),
      fetch('/api/epicenter').then(r=>r.json()),fetch('/api/summary').then(r=>r.json())
    ]);
    edgeLayer.addData(edges).addTo(map); nodeLayer.addData(nodes).addTo(map); epicenterLayer.addData(epi).addTo(map);
    L.control.layers({'Enlaces':edgeLayer},{'Nodos estratégicos':nodeLayer,'Epicentro 27F':epicenterLayer},{collapsed:false}).addTo(map);
    addLegend();
    document.getElementById('n-edges').textContent=fmt(summary.aristas);
    document.getElementById('n-nodes').textContent=fmt(summary.nodos);
    document.getElementById('n-covered').textContent=fmt(summary.con_estimacion);
    document.getElementById('n-uncovered').textContent=fmt(summary.sin_estimacion);
    renderAttr(summary.attr);
    const bounds=edgeLayer.getBounds(); if(bounds.isValid()) map.fitBounds(bounds.pad(0.04));
    document.getElementById('loading').style.display='none';
  }catch(err){
    document.getElementById('loading').textContent='Error al cargar datos: '+err.message;
    console.error(err);
  }
}
load();
