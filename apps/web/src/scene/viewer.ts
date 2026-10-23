/**
 * Three.js scene viewer for Parallel scenes. Canonical frame: right-handed, Z up, metres.
 * Prepared-model objects are drawn as simplified analytical geometry; visual assets (point clouds,
 * Gaussian splats) are drawn as captured. Matrices from the scene's coordinate transforms are
 * row-major 4x4 with column vectors, exactly as Three's Matrix4.set expects.
 */
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { PLYLoader } from "three/examples/jsm/loaders/PLYLoader.js";
import { CSS2DObject, CSS2DRenderer } from "three/examples/jsm/renderers/CSS2DRenderer.js";
import type { Geometry, SpatialObject } from "../api/types";

export type ViewMode = "captured" | "model" | "combined";
export interface PlaybackFrame {
  t: number;
  i: number[];
  xy: number[];
  angle?: number[];
}
export interface PlaybackData {
  agents: string[];
  kind?: string;
  frames: PlaybackFrame[];
  note?: string;
}
export interface Marker {
  id: string;
  label: string;
  position?: number[] | null;
  geometry?: Geometry | null;
  tone: "observation" | "interpretation";
}

interface Style {
  color: number;
  opacity: number;
  height?: number;
  outline: number;
}
const STYLE: Record<string, Style> = {
  walkable_area: { color: 0x2a78d6, opacity: 0.16, outline: 0x3987e5 },
  obstacle: { color: 0x6b7a90, opacity: 0.95, height: 1.8, outline: 0x9aa7ba },
  exit: { color: 0x0ca30c, opacity: 0.55, outline: 0x4fd34f },
  refuge: { color: 0x199e70, opacity: 0.55, outline: 0x34d39b },
  start_zone: { color: 0xeda100, opacity: 0.3, outline: 0xfab219 },
  route_segment: { color: 0xeb6834, opacity: 0.22, outline: 0xeb6834 },
  road: { color: 0x9aa7ba, opacity: 1, outline: 0xc3cfe0 },
  conduit: { color: 0x3987e5, opacity: 1, outline: 0x6da7ec },
  junction: { color: 0x3987e5, opacity: 1, outline: 0x6da7ec },
  node: { color: 0x9aa7ba, opacity: 1, outline: 0x9aa7ba },
  outfall: { color: 0x199e70, opacity: 1, outline: 0x34d39b },
};
const DEFAULT_STYLE: Style = { color: 0x00a5fd, opacity: 0.25, outline: 0x00a5fd };
const HYPOTHETICAL = 0x9085e9;
const SELECTED = 0xffffff;
const AGENT = 0x00a5fd;

function cssColor(name: string, fallback: string): THREE.Color {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  try {
    return new THREE.Color(v || fallback);
  } catch {
    return new THREE.Color(fallback);
  }
}

/** A round step (1, 2 or 5 x 10^k) giving roughly `target` divisions over `extent`. */
function niceStep(extent: number, target = 12): number {
  const raw = Math.max(extent, 1) / target;
  const p = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 5, 10].map((m) => m * p).find((v) => v >= raw) ?? 10 * p;
}

/** Horizontal extent of prepared objects, used to size lines and points so networks stay legible at any scale. */
function objectsExtent(objects: SpatialObject[]): number {
  const box = new THREE.Box2();
  const visit = (c: unknown): void => {
    if (Array.isArray(c) && typeof c[0] === "number") box.expandByPoint(new THREE.Vector2(c[0], c[1] as number));
    else if (Array.isArray(c)) c.forEach(visit);
  };
  for (const o of objects) {
    const g = o.geometry as unknown as { coordinates?: unknown; center?: number[] };
    visit(g.coordinates ?? g.center);
  }
  if (box.isEmpty()) return 50;
  const s = box.getSize(new THREE.Vector2());
  return Math.max(s.x, s.y, 4);
}

/** Bounds of a point cloud between the 2nd and 98th percentiles, so stray SfM points do not shrink the view. */
function robustBox(pts: THREE.Points): THREE.Box3 {
  const pos = pts.geometry.getAttribute("position");
  const n = pos.count;
  const box = new THREE.Box3();
  if (n < 50) return box.setFromObject(pts);
  pts.updateWorldMatrix(true, false);
  const step = Math.max(1, Math.floor(n / 20000));
  const xs: number[] = [], ys: number[] = [], zs: number[] = [];
  const v = new THREE.Vector3();
  for (let k = 0; k < n; k += step) {
    v.fromBufferAttribute(pos, k).applyMatrix4(pts.matrixWorld);
    xs.push(v.x);
    ys.push(v.y);
    zs.push(v.z);
  }
  const q = (a: number[], f: number) => a.sort((x, y) => x - y)[Math.min(a.length - 1, Math.floor(f * a.length))];
  box.min.set(q(xs, 0.02), q(ys, 0.02), q(zs, 0.02));
  box.max.set(q(xs, 0.98), q(ys, 0.98), q(zs, 0.98));
  return box;
}

const LABEL_PRIORITY: [string, number][] = [["measure", 0], ["warn", 1], ["evidence", 2]];

function ringToShape(ring: number[][]): THREE.Shape {
  return new THREE.Shape(ring.map(([x, y]) => new THREE.Vector2(x, y)));
}
function polygonShape(rings: number[][][]): THREE.Shape {
  const s = ringToShape(rings[0]);
  for (const hole of rings.slice(1)) s.holes.push(new THREE.Path(hole.map(([x, y]) => new THREE.Vector2(x, y))));
  return s;
}

export class SceneViewer {
  readonly renderer: THREE.WebGLRenderer;
  readonly scene = new THREE.Scene();
  readonly camera: THREE.PerspectiveCamera;
  readonly controls: OrbitControls;
  private labels: CSS2DRenderer;
  private prepared = new THREE.Group();
  private visual = new THREE.Group();
  private playback = new THREE.Group();
  private measureGroup = new THREE.Group();
  private markers = new THREE.Group();
  private grid: THREE.GridHelper | null = null;
  private labelsDirty = true;
  /** Spacing of the ground grid in metres, shown as the scale note. */
  gridSpacing: number | null = null;
  /** Scale for line and point symbols, from the extent of the prepared objects. */
  private symbol = { tube: 0.25, point: 1.2 };
  private agents: THREE.InstancedMesh | null = null;
  private play: PlaybackData | null = null;
  private raf = 0;
  private ro: ResizeObserver;
  private themeWatch: MutationObserver;
  private schemeQuery = window.matchMedia("(prefers-color-scheme: dark)");
  private onScheme = () => this.applyTheme();
  private pickables: THREE.Object3D[] = [];
  private down: { x: number; y: number } | null = null;
  private spark: THREE.Object3D | null = null;
  mode: "select" | "measure" = "select";
  measurePoints: THREE.Vector3[] = [];
  onPick?: (id: string | null) => void;
  onMeasure?: (a: THREE.Vector3, b: THREE.Vector3) => void;
  onCameraChange?: () => void;
  onGrid?: (spacing: number) => void;

  constructor(private el: HTMLElement) {
    this.renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    el.appendChild(this.renderer.domElement);
    this.renderer.domElement.style.display = "block";
    this.labels = new CSS2DRenderer();
    Object.assign(this.labels.domElement.style, { position: "absolute", inset: "0", pointerEvents: "none" });
    el.appendChild(this.labels.domElement);
    this.camera = new THREE.PerspectiveCamera(50, 1, 0.05, 20000);
    this.camera.up.set(0, 0, 1);
    this.camera.position.set(40, -60, 50);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    this.controls.addEventListener("change", () => {
      this.labelsDirty = true;
      this.onCameraChange?.();
    });
    this.scene.add(new THREE.HemisphereLight(0xdde8ff, 0x1a2333, 1.6));
    const sun = new THREE.DirectionalLight(0xffffff, 1.4);
    sun.position.set(50, -40, 120);
    this.scene.add(sun, this.visual, this.prepared, this.markers, this.playback, this.measureGroup);
    const c = this.renderer.domElement;
    c.addEventListener("pointerdown", (e) => (this.down = { x: e.clientX, y: e.clientY }));
    c.addEventListener("pointerup", (e) => this.onClick(e));
    // follow the theme: the OS preference and the explicit theme toggle both change the tokens
    this.themeWatch = new MutationObserver(() => this.applyTheme());
    this.themeWatch.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    this.schemeQuery.addEventListener("change", this.onScheme);
    this.applyTheme();
    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(el);
    this.resize();
    const loop = () => {
      this.raf = requestAnimationFrame(loop);
      this.controls.update();
      this.renderer.render(this.scene, this.camera);
      this.labels.render(this.scene, this.camera);
      if (this.labelsDirty) this.declutter();
    };
    loop();
  }

  dispose(): void {
    cancelAnimationFrame(this.raf);
    this.ro.disconnect();
    this.themeWatch.disconnect();
    this.schemeQuery.removeEventListener("change", this.onScheme);
    this.controls.dispose();
    this.renderer.dispose();
    this.el.innerHTML = "";
  }

  private applyTheme(): void {
    this.renderer.setClearColor(cssColor("--surface-sunken", "#08162d"));
    if (this.grid) this.setGridColors(this.grid);
  }

  private setGridColors(grid: THREE.GridHelper): void {
    const major = cssColor("--scene-grid-major", "#2b4467");
    const minor = cssColor("--scene-grid-minor", "#1a2c48");
    // GridHelper bakes colours per vertex: the two centre lines use the first colour, the rest the second
    const col = grid.geometry.getAttribute("color") as THREE.BufferAttribute;
    const centre = new THREE.Color(major);
    for (let i = 0; i < col.count; i++) {
      const p = grid.geometry.getAttribute("position");
      const onAxis = Math.abs(p.getX(i)) < 1e-6 || Math.abs(p.getZ(i)) < 1e-6;
      const c = onAxis ? centre : minor;
      col.setXYZ(i, c.r, c.g, c.b);
    }
    col.needsUpdate = true;
  }

  private resize(): void {
    const w = this.el.clientWidth || 600;
    const h = this.el.clientHeight || 400;
    this.renderer.setSize(w, h);
    this.labels.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.labelsDirty = true;
  }

  /** Hide labels that would overlap a higher-priority one; the object list and selection still name everything. */
  private declutter(): void {
    this.labelsDirty = false;
    const rank = (el: Element) => LABEL_PRIORITY.find(([c]) => el.classList.contains(c))?.[1] ?? 3;
    const els = Array.from(this.labels.domElement.querySelectorAll<HTMLElement>(".scene-label"))
      .filter((el) => el.parentElement?.style.display !== "none" && el.style.display !== "none")
      .sort((a, b) => rank(a) - rank(b));
    const kept: DOMRect[] = [];
    for (const el of els) {
      el.style.visibility = "";
      const r = el.getBoundingClientRect();
      const clash = kept.some((k) => r.left < k.right + 2 && r.right > k.left - 2 && r.top < k.bottom + 2 && r.bottom > k.top - 2);
      if (clash) el.style.visibility = "hidden";
      else kept.push(r);
    }
  }

  private ndc(e: PointerEvent): THREE.Vector2 {
    const r = this.renderer.domElement.getBoundingClientRect();
    return new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
  }

  private onClick(e: PointerEvent): void {
    if (!this.down || Math.hypot(e.clientX - this.down.x, e.clientY - this.down.y) > 4) return;
    const ray = new THREE.Raycaster();
    ray.params.Points = { threshold: 0.15 };
    ray.setFromCamera(this.ndc(e), this.camera);
    if (this.mode === "measure") {
      const hits = ray.intersectObjects([...this.pickables, ...this.visual.children], true);
      const p = hits[0]?.point ?? ray.ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 0, 1), 0), new THREE.Vector3());
      if (!p) return;
      this.measurePoints = this.measurePoints.length >= 2 ? [p.clone()] : [...this.measurePoints, p.clone()];
      this.drawMeasure();
      if (this.measurePoints.length === 2) this.onMeasure?.(this.measurePoints[0], this.measurePoints[1]);
      return;
    }
    const hit = ray.intersectObjects(this.pickables, true)[0];
    let o: THREE.Object3D | null = hit?.object ?? null;
    while (o && !o.userData.objId) o = o.parent;
    this.onPick?.(o?.userData.objId ?? null);
  }

  private drawMeasure(): void {
    this.measureGroup.clear();
    const pts = this.measurePoints;
    for (const p of pts) {
      const m = new THREE.Mesh(new THREE.SphereGeometry(0.12, 16, 12), new THREE.MeshBasicMaterial({ color: 0xffffff }));
      m.position.copy(p);
      this.measureGroup.add(m);
    }
    if (pts.length === 2) {
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: 0xffffff }));
      this.measureGroup.add(line);
      this.measureGroup.add(this.label(`${pts[0].distanceTo(pts[1]).toFixed(2)} m`, pts[0].clone().lerp(pts[1], 0.5), "measure"));
    }
  }

  clearMeasure(): void {
    this.measurePoints = [];
    this.measureGroup.clear();
  }

  private label(text: string, at: THREE.Vector3, cls = ""): CSS2DObject {
    const div = document.createElement("div");
    div.className = `scene-label ${cls}`;
    div.textContent = text; // labels are data: never inserted as HTML
    const o = new CSS2DObject(div);
    o.position.copy(at);
    o.center.set(0.5, 1); // the label sits above its anchor rather than covering it
    this.labelsDirty = true;
    return o;
  }

  /** Prepared-model objects, by layer and belonging. */
  setObjects(objects: SpatialObject[], opts: { hiddenLayers: Set<string>; mode: ViewMode; selected: string | null; showLabels: boolean }): void {
    this.prepared.clear();
    this.pickables = [];
    this.prepared.visible = opts.mode !== "captured";
    this.visual.visible = opts.mode !== "model";
    const extent = objectsExtent(objects);
    this.symbol = { tube: THREE.MathUtils.clamp(extent / 450, 0.25, 3), point: THREE.MathUtils.clamp(extent / 70, 0.6, 8) };
    objects.forEach((o, idx) => {
      if (opts.hiddenLayers.has(o.layer)) return;
      const hyp = o.belongs_to === "hypothetical_scenario";
      const style = STYLE[o.kind] ?? DEFAULT_STYLE;
      const g = this.geometryGroup(o.geometry, { ...style, color: hyp ? HYPOTHETICAL : style.color, outline: hyp ? HYPOTHETICAL : style.outline },
        0.01 + (idx % 50) * 0.002, o.id === opts.selected, hyp);
      g.userData.objId = o.id;
      this.prepared.add(g);
      this.pickables.push(g);
      if (o.embedded_in_capture && o.captured_geometry) {
        // the object moved in a scenario but its appearance is still baked into the capture: show both
        const ghost = this.geometryGroup(o.captured_geometry, { color: 0xd03b3b, opacity: 0.12, outline: 0xd03b3b }, 0.02, false, true);
        this.prepared.add(ghost);
        const c = new THREE.Box3().setFromObject(ghost).getCenter(new THREE.Vector3());
        this.prepared.add(this.label("Captured position still visible in the reconstruction", c, "warn"));
      }
      if (opts.showLabels && ["exit", "refuge", "start_zone", "junction", "outfall"].includes(o.kind) || (opts.showLabels && hyp)) {
        const c = new THREE.Box3().setFromObject(g).getCenter(new THREE.Vector3());
        c.z += Math.max(1.2, this.symbol.point);
        this.prepared.add(this.label(hyp ? `${o.name} (hypothetical)` : o.name, c));
      }
    });
    if (!this.grid) this.fit();
  }

  private geometryGroup(geom: Geometry, s: Style, z: number, selected: boolean, dashed: boolean): THREE.Group {
    const g = new THREE.Group();
    const outline = selected ? SELECTED : s.outline;
    const lineMat = dashed
      ? new THREE.LineDashedMaterial({ color: outline, dashSize: 0.6, gapSize: 0.4 })
      : new THREE.LineBasicMaterial({ color: outline });
    const addPolygon = (rings: number[][][]) => {
      const shape = polygonShape(rings);
      if (s.height) {
        const mesh = new THREE.Mesh(new THREE.ExtrudeGeometry(shape, { depth: s.height, bevelEnabled: false }),
          new THREE.MeshStandardMaterial({ color: selected ? 0xb9d9ff : s.color, transparent: s.opacity < 1, opacity: s.opacity, roughness: 0.9 }));
        g.add(mesh);
      } else {
        const mesh = new THREE.Mesh(new THREE.ShapeGeometry(shape),
          new THREE.MeshBasicMaterial({ color: s.color, transparent: true, opacity: selected ? Math.min(0.6, s.opacity + 0.25) : s.opacity, side: THREE.DoubleSide, depthWrite: false }));
        mesh.position.z = z;
        g.add(mesh);
      }
      const ring = rings[0].map(([x, y]) => new THREE.Vector3(x, y, z + 0.005 + (s.height ?? 0)));
      const line = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(ring), lineMat);
      if (dashed) line.computeLineDistances();
      g.add(line);
    };
    if (geom.type === "Polygon") addPolygon(geom.coordinates as number[][][]);
    else if (geom.type === "MultiPolygon") (geom.coordinates as number[][][][]).forEach(addPolygon);
    else if (geom.type === "LineString") {
      const pts = (geom.coordinates as number[][]).map(([x, y, zz]) => new THREE.Vector3(x, y, (zz ?? 0) + z + 0.05));
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), lineMat);
      if (dashed) line.computeLineDistances();
      g.add(line);
      // a thin ribbon so lines remain pickable and visible at distance
      const curve = new THREE.CatmullRomCurve3(pts, false, "catmullrom", 0);
      g.add(new THREE.Mesh(new THREE.TubeGeometry(curve, Math.max(2, pts.length * 4), this.symbol.tube, 6, false),
        new THREE.MeshBasicMaterial({ color: selected ? SELECTED : s.color, transparent: true, opacity: 0.85 })));
    } else if (geom.type === "Point") {
      const [x, y, zz] = geom.coordinates as number[];
      const r = this.symbol.point;
      const m = new THREE.Mesh(new THREE.SphereGeometry(r, 20, 14), new THREE.MeshStandardMaterial({ color: selected ? SELECTED : s.color }));
      m.position.set(x, y, (zz ?? 0) + r);
      g.add(m);
    } else if (geom.type === "Box") {
      const b = geom as unknown as { center: number[]; size: number[]; yaw_deg: number };
      const m = new THREE.Mesh(new THREE.BoxGeometry(b.size[0], b.size[1], b.size[2]),
        new THREE.MeshStandardMaterial({ color: selected ? 0xb9d9ff : s.color, transparent: true, opacity: 0.7 }));
      m.position.set(b.center[0], b.center[1], b.center[2] ?? b.size[2] / 2);
      m.rotation.z = THREE.MathUtils.degToRad(b.yaw_deg ?? 0);
      g.add(m);
    }
    return g;
  }

  async addPointCloud(id: string, bytes: ArrayBuffer, matrix: number[][] | null): Promise<void> {
    const geom = new PLYLoader().parse(bytes);
    const hasColor = !!geom.getAttribute("color");
    const pts = new THREE.Points(geom, new THREE.PointsMaterial({ size: 0.06, vertexColors: hasColor, color: hasColor ? 0xffffff : 0x9ec5f4 }));
    pts.name = id;
    this.applyMatrix(pts, matrix);
    this.visual.add(pts);
    this.fit();
  }

  /** Gaussian splats in any format Spark reads (.ply, .splat, .spz, .ksplat); the file name selects the parser. */
  async addSplat(id: string, bytes: ArrayBuffer, matrix: number[][] | null, fileName = `${id}.ply`): Promise<void> {
    const { SparkRenderer, SplatMesh } = await import("@sparkjsdev/spark");
    if (!this.spark) {
      this.spark = new SparkRenderer({ renderer: this.renderer }) as unknown as THREE.Object3D;
      this.scene.add(this.spark);
    }
    const mesh = new SplatMesh({ fileBytes: new Uint8Array(bytes), fileName });
    await mesh.initialized;
    mesh.name = id;
    this.applyMatrix(mesh as unknown as THREE.Object3D, matrix);
    this.visual.add(mesh as unknown as THREE.Object3D);
    this.fit();
  }

  setVisualVisible(name: string, visible: boolean): void {
    const o = this.visual.getObjectByName(name);
    if (o) o.visible = visible;
  }

  private applyMatrix(o: THREE.Object3D, m: number[][] | null): void {
    if (!m) return;
    const mat = new THREE.Matrix4().set(...(m.flat() as [number, number, number, number, number, number, number, number, number, number, number, number, number, number, number, number]));
    o.applyMatrix4(mat);
  }

  setMarkers(markers: Marker[]): void {
    this.markers.clear();
    for (const mk of markers) {
      const color = mk.tone === "observation" ? 0xe87ba4 : 0x9085e9;
      if (mk.geometry?.type === "LineString") {
        const pts = (mk.geometry.coordinates as number[][]).map(([x, y]) => new THREE.Vector3(x, y, 0.06));
        this.markers.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color })));
        this.markers.add(this.label(mk.label, pts[pts.length - 1].clone().setZ(0.8), "evidence"));
      } else if (mk.position) {
        const p = new THREE.Vector3(mk.position[0], mk.position[1], 0);
        const cone = new THREE.Mesh(new THREE.ConeGeometry(0.25, 0.8, 16), new THREE.MeshStandardMaterial({ color }));
        cone.rotation.x = -Math.PI / 2;
        cone.position.copy(p).setZ(0.4);
        this.markers.add(cone, this.label(mk.label, p.clone().setZ(1.2), "evidence"));
      }
    }
  }

  setPlayback(data: PlaybackData | null): void {
    this.playback.clear();
    this.agents = null;
    this.play = data;
    if (!data || !data.frames.length) return;
    const vehicles = data.kind === "vehicles";
    const geo = vehicles ? new THREE.BoxGeometry(4.5, 1.8, 1.5) : new THREE.CylinderGeometry(0.25, 0.25, 1.7, 12).rotateX(Math.PI / 2);
    this.agents = new THREE.InstancedMesh(geo, new THREE.MeshStandardMaterial({ color: AGENT, roughness: 0.5 }), Math.max(1, data.agents.length));
    this.agents.frustumCulled = false;
    this.playback.add(this.agents);
    this.setTime(data.frames[0].t);
  }

  get playbackRange(): [number, number] | null {
    const f = this.play?.frames;
    return f && f.length ? [f[0].t, f[f.length - 1].t] : null;
  }

  setTime(t: number): number {
    const p = this.play;
    if (!p || !this.agents) return 0;
    const f = p.frames;
    let lo = 0, hi = f.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (f[mid].t <= t) lo = mid;
      else hi = mid - 1;
    }
    const a = f[lo], b = f[Math.min(lo + 1, f.length - 1)];
    const w = b.t > a.t ? Math.min(1, Math.max(0, (t - a.t) / (b.t - a.t))) : 0;
    const next = new Map<number, [number, number]>();
    b.i.forEach((id, k) => next.set(id, [b.xy[2 * k], b.xy[2 * k + 1]]));
    const m = new THREE.Matrix4();
    const zero = new THREE.Matrix4().makeScale(0, 0, 0);
    for (let k = 0; k < this.agents.count; k++) this.agents.setMatrixAt(k, zero);
    const vehicles = p.kind === "vehicles";
    a.i.forEach((id, k) => {
      let x = a.xy[2 * k], y = a.xy[2 * k + 1];
      const n = next.get(id);
      if (n) {
        x += (n[0] - x) * w;
        y += (n[1] - y) * w;
      }
      const yaw = vehicles && a.angle ? THREE.MathUtils.degToRad(90 - a.angle[k]) : 0; // SUMO angle: clockwise from north
      m.makeRotationZ(yaw).setPosition(x, y, vehicles ? 0.75 : 0.85);
      this.agents!.setMatrixAt(id, m);
    });
    this.agents.instanceMatrix.needsUpdate = true;
    return a.i.length;
  }

  /** Bounds of everything drawn, with point clouds trimmed to their dense core. */
  private contentBox(withMarkers = true): THREE.Box3 {
    const box = new THREE.Box3();
    const groups = withMarkers ? [this.prepared, this.visual, this.markers] : [this.prepared, this.visual];
    for (const g of groups) {
      for (const child of g.children) {
        const b = child instanceof THREE.Points ? robustBox(child) : new THREE.Box3().setFromObject(child);
        if (!b.isEmpty()) box.union(b);
      }
    }
    return box;
  }

  fit(): void {
    const box = this.contentBox();
    if (box.isEmpty()) return;
    const c = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3());
    const d = Math.max(size.x, size.y, 4);
    // frame the bounding sphere in both the vertical and the horizontal field of view
    const radius = Math.max(size.length() / 2, 2);
    const vfov = THREE.MathUtils.degToRad(this.camera.fov) / 2;
    const hfov = Math.atan(Math.tan(vfov) * this.camera.aspect);
    const dist = (radius / Math.sin(Math.min(vfov, hfov))) * 1.05;
    this.controls.target.copy(c);
    this.camera.position.copy(c).addScaledVector(new THREE.Vector3(0.35, -0.75, 0.6).normalize(), dist);
    this.camera.near = d / 2000;
    this.camera.far = d * 50;
    this.camera.updateProjectionMatrix();
    // a ground grid sized to the content, in round metre steps, so it reads as scale rather than texture
    if (this.grid) {
      this.scene.remove(this.grid);
      this.grid.dispose();
    }
    const step = niceStep(d);
    const n = Math.ceil((d * 1.6) / step) * step;
    this.grid = new THREE.GridHelper(n, Math.round(n / step));
    this.setGridColors(this.grid);
    this.grid.rotation.x = Math.PI / 2;
    this.grid.position.set(Math.round(c.x / step) * step, Math.round(c.y / step) * step, Math.min(0, box.min.z) - 0.01);
    this.scene.add(this.grid);
    this.gridSpacing = step;
    this.labelsDirty = true;
    this.onGrid?.(step);
  }

  topDown(): void {
    const box = this.contentBox(false);
    const c = box.isEmpty() ? new THREE.Vector3() : box.getCenter(new THREE.Vector3());
    const size = box.isEmpty() ? new THREE.Vector3(50, 50, 0) : box.getSize(new THREE.Vector3());
    const d = Math.max(size.x, size.y) * 1.1;
    this.controls.target.copy(c);
    this.camera.position.set(c.x, c.y - d * 0.001, c.z + d);
  }

  getView(): { position: number[]; target: number[] } {
    return { position: this.camera.position.toArray(), target: this.controls.target.toArray() };
  }

  setView(v: { position: number[]; target: number[] }): void {
    this.camera.position.fromArray(v.position);
    this.controls.target.fromArray(v.target);
  }

  screenshot(): Promise<Blob | null> {
    this.renderer.render(this.scene, this.camera);
    return new Promise((res) => this.renderer.domElement.toBlob((b) => res(b), "image/png"));
  }
}
