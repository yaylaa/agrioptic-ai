// Same fixed -0.2..1 scale as the backend Sentinel raster; never stretch per date.
const stops=[[.69,.32,.19],[.88,.70,.36],[.83,.86,.54],[.36,.65,.42],[.05,.34,.24]];
export function indexColor(value:number){const t=Math.max(0,Math.min(1,(value+.2)/1.2))*4;const i=Math.min(3,Math.floor(t));return `rgb(${stops[i].map((v,k)=>Math.round(255*(v+(stops[i+1][k]-v)*(t-i)))).join(',')})`}
