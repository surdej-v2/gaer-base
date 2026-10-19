/* @ds-bundle: {"format":3,"namespace":"HappyMatesDesignSystem_019e2c","components":[],"sourceHashes":{"ui_kits/web/components.jsx":"3d699e9eb1e9","ui_kits/web/screens.jsx":"8cb3bfa72fc5"},"inlinedExternals":[],"unexposedExports":[]} */

(() => {

const __ds_ns = (window.HappyMatesDesignSystem_019e2c = window.HappyMatesDesignSystem_019e2c || {});

const __ds_scope = {};

(__ds_ns.__errors = __ds_ns.__errors || []);

// ui_kits/web/components.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
// Happy Mates UI kit — shared components
// Pinned to the production codebase (happy-mates/web)

const {
  useState,
  useEffect,
  useRef
} = React;
const Icon = ({
  name,
  size = 16,
  color,
  style,
  ...rest
}) => /*#__PURE__*/React.createElement("i", _extends({
  "data-lucide": name,
  style: {
    width: size,
    height: size,
    color,
    ...style
  }
}, rest));
const cx = (...xs) => xs.filter(Boolean).join(" ");

// ───────────────────────────────────────────────────── Button
function Button({
  variant = "primary",
  size = "default",
  children,
  className,
  ...rest
}) {
  const variantClass = {
    primary: "hm-btn hm-btn-primary",
    outline: "hm-btn hm-btn-outline",
    ghost: "hm-btn hm-btn-ghost",
    success: "hm-btn hm-btn-success"
  }[variant] || "hm-btn";
  const sizeClass = size === "lg" ? "hm-btn-lg" : size === "sm" ? "hm-btn-sm" : "";
  return /*#__PURE__*/React.createElement("button", _extends({
    className: cx(variantClass, sizeClass, className)
  }, rest), children);
}

// ───────────────────────────────────────────────────── Gradient glow wrapper
function Glow({
  from,
  to,
  opacity = 0.25,
  hoverOpacity = 0.4,
  radius = 18,
  children,
  className,
  style
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: cx("hm-glow", className),
    style: {
      position: "relative",
      isolation: "isolate",
      ...style
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "hm-glow__ring",
    style: {
      position: "absolute",
      inset: -4,
      borderRadius: radius,
      background: `linear-gradient(90deg, ${from}, ${to})`,
      filter: "blur(12px)",
      opacity,
      zIndex: -1,
      transition: "opacity 300ms cubic-bezier(.4,0,.2,1)"
    }
  }), children);
}

// ───────────────────────────────────────────────────── Wordmark / Logo
function Logo({
  size = 40
}) {
  return /*#__PURE__*/React.createElement("img", {
    src: "../../assets/happy-mates-logo.svg",
    alt: "",
    width: size,
    height: size
  });
}
function Wordmark({
  size = 20
}) {
  return /*#__PURE__*/React.createElement("span", {
    className: "hm-wordmark",
    style: {
      fontSize: size,
      fontWeight: 800,
      letterSpacing: ".03em"
    }
  }, "HAPPY MATES");
}

// ───────────────────────────────────────────────────── Nav (sticky top)
function Nav({
  active = "home",
  onNavigate
}) {
  const [days, setDays] = useState(184);
  useEffect(() => {
    const t = new Date("2026-09-15T08:40:00").getTime();
    const tick = () => {
      const diff = t - Date.now();
      if (diff > 0) setDays(Math.floor(diff / 86400000));
    };
    tick();
    const id = setInterval(tick, 60_000);
    return () => clearInterval(id);
  }, []);
  const link = (id, label) => /*#__PURE__*/React.createElement("a", {
    key: id,
    onClick: () => onNavigate?.(id),
    style: {
      cursor: "pointer",
      fontSize: 14,
      textDecoration: "none",
      color: active === id ? "var(--hm-primary)" : "var(--hm-foreground)",
      transition: "color 150ms"
    },
    onMouseEnter: e => e.currentTarget.style.color = "var(--hm-primary)",
    onMouseLeave: e => e.currentTarget.style.color = active === id ? "var(--hm-primary)" : "var(--hm-foreground)"
  }, label);
  return /*#__PURE__*/React.createElement("nav", {
    style: {
      position: "sticky",
      top: 0,
      zIndex: 50,
      background: "white",
      borderBottom: "1px solid var(--hm-border)",
      padding: "14px 24px",
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      backdropFilter: "saturate(180%) blur(4px)"
    }
  }, /*#__PURE__*/React.createElement("a", {
    onClick: () => onNavigate?.("home"),
    style: {
      display: "flex",
      gap: 12,
      alignItems: "center",
      cursor: "pointer",
      textDecoration: "none"
    }
  }, /*#__PURE__*/React.createElement(Logo, {
    size: 36
  }), /*#__PURE__*/React.createElement(Wordmark, {
    size: 18
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      gap: 24,
      alignItems: "center"
    }
  }, link("unge", "Curly Brain?"), link("companies", "Need AI?"), link("hearts", "Warm Heart?"), link("about", "About Us"), /*#__PURE__*/React.createElement("span", {
    style: {
      display: "inline-flex",
      gap: 6,
      padding: "6px 10px",
      background: "rgb(37 99 235 / .12)",
      color: "var(--hm-primary)",
      borderRadius: 8,
      fontSize: 13,
      fontWeight: 500,
      alignItems: "center"
    }
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "calendar",
    size: 14
  }), days, "d"), /*#__PURE__*/React.createElement("span", {
    style: {
      display: "inline-flex",
      gap: 4,
      alignItems: "center",
      fontSize: 13
    }
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "globe",
    size: 14,
    style: {
      opacity: 0.6
    }
  }), "EN"), /*#__PURE__*/React.createElement(Button, {
    variant: "primary"
  }, "Contact Us")));
}

// ───────────────────────────────────────────────────── Footer
function Footer({
  onNavigate
}) {
  const col = (h, items) => /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("h4", {
    style: {
      fontSize: 14,
      fontWeight: 600,
      margin: "0 0 10px"
    }
  }, h), /*#__PURE__*/React.createElement("ul", {
    style: {
      listStyle: "none",
      margin: 0,
      padding: 0
    }
  }, items.map(i => /*#__PURE__*/React.createElement("li", {
    key: i.label,
    style: {
      fontSize: 13,
      padding: "4px 0"
    }
  }, /*#__PURE__*/React.createElement("a", {
    onClick: () => i.to && onNavigate?.(i.to),
    style: {
      color: "var(--hm-muted-foreground)",
      textDecoration: "none",
      cursor: i.to ? "pointer" : "default"
    }
  }, i.label)))));
  return /*#__PURE__*/React.createElement("footer", {
    style: {
      background: "rgb(241 245 249 / .5)",
      borderTop: "1px solid var(--hm-border)",
      padding: "48px 24px 24px"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      maxWidth: 1200,
      margin: "0 auto",
      display: "grid",
      gridTemplateColumns: "1.4fr 1fr 1fr 1fr 1fr",
      gap: 32
    }
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      gap: 10,
      alignItems: "center",
      marginBottom: 12
    }
  }, /*#__PURE__*/React.createElement(Logo, {
    size: 28
  }), /*#__PURE__*/React.createElement("b", {
    style: {
      fontSize: 16
    }
  }, "Happy Mates")), /*#__PURE__*/React.createElement("p", {
    style: {
      fontSize: 13,
      lineHeight: 1.55,
      color: "var(--hm-muted-foreground)",
      margin: 0,
      maxWidth: 280
    }
  }, "Bridging the gap between young AI talent and forward-thinking companies.")), col("For You", [{
    label: "Individuals"
  }, {
    label: "Companies",
    to: "companies"
  }, {
    label: "Dele Mate"
  }, {
    label: "Daily Workflow"
  }, {
    label: "Contact"
  }]), col("Platform", [{
    label: "The Sourdough"
  }, {
    label: "Recipes",
    to: "recipes"
  }, {
    label: "Plans & Process"
  }, {
    label: "Daily Workflow"
  }]), col("Resources", [{
    label: "About Us"
  }, {
    label: "AI Specialisation"
  }, {
    label: "Training"
  }, {
    label: "Videos"
  }, {
    label: "Print Hub"
  }, {
    label: "Blog"
  }]), col("Events", [{
    label: "All Events"
  }, {
    label: "Playitas Bootcamp 2026"
  }])), /*#__PURE__*/React.createElement("div", {
    style: {
      maxWidth: 1200,
      margin: "48px auto 0",
      paddingTop: 24,
      borderTop: "1px solid var(--hm-border)",
      textAlign: "center",
      fontSize: 13,
      color: "var(--hm-muted-foreground)"
    }
  }, "\xA9 2026 Happy Mates. All rights reserved."));
}

// ───────────────────────────────────────────────────── Section eyebrow
function Eyebrow({
  children,
  color = "primary"
}) {
  const c = color === "orange" ? {
    bg: "rgb(249 115 22 / .14)",
    fg: "#ea580c"
  } : {
    bg: "rgb(37 99 235 / .12)",
    fg: "var(--hm-primary)"
  };
  return /*#__PURE__*/React.createElement("span", {
    style: {
      display: "inline-flex",
      alignItems: "center",
      gap: 8,
      padding: "4px 12px",
      borderRadius: 999,
      background: c.bg,
      color: c.fg,
      fontSize: 13,
      fontWeight: 500
    }
  }, children);
}

// ───────────────────────────────────────────────────── Welcome hero (home page)
function WelcomeHero({
  onSeeMore
}) {
  return /*#__PURE__*/React.createElement("section", {
    style: {
      position: "relative",
      minHeight: 720,
      display: "flex",
      flexDirection: "column",
      justifyContent: "center",
      padding: "0 24px",
      overflow: "hidden"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      position: "absolute",
      inset: 0,
      zIndex: 0,
      backgroundImage: "url(../../assets/happy-mates-team.jpeg)",
      backgroundSize: "cover",
      backgroundPosition: "center"
    }
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      position: "absolute",
      inset: 0,
      zIndex: 1,
      background: "linear-gradient(180deg, rgba(0,0,0,.7), rgba(0,0,0,.5) 50%, rgba(0,0,0,.8))"
    }
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      position: "relative",
      zIndex: 10,
      maxWidth: 1200,
      margin: "0 auto",
      width: "100%",
      textAlign: "center",
      padding: "80px 0"
    }
  }, /*#__PURE__*/React.createElement("h1", {
    style: {
      fontSize: "clamp(48px, 7vw, 96px)",
      fontWeight: 700,
      letterSpacing: "-.025em",
      color: "white",
      margin: 0,
      lineHeight: 1.05,
      textShadow: "0 4px 24px rgba(0,0,0,.5)"
    }
  }, "We are ", /*#__PURE__*/React.createElement("span", {
    className: "hm-wordmark",
    style: {
      fontSize: "inherit"
    }
  }, "Happy Mates!")), /*#__PURE__*/React.createElement("p", {
    style: {
      marginTop: 20,
      fontSize: 22,
      color: "rgba(255,255,255,.9)",
      fontWeight: 500
    }
  }, "Ready to help you in your Digital Kitchen"), /*#__PURE__*/React.createElement("p", {
    style: {
      marginTop: 36,
      fontSize: 18,
      color: "rgba(255,255,255,.8)",
      maxWidth: 600,
      marginInline: "auto"
    }
  }, "Take control with full digital sovereignty."), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 32
    }
  }, /*#__PURE__*/React.createElement("a", {
    style: {
      display: "inline-flex",
      alignItems: "center",
      gap: 12,
      background: "linear-gradient(90deg,#a855f7,#d946ef)",
      color: "white",
      padding: "16px 32px",
      borderRadius: 16,
      fontSize: 18,
      fontWeight: 600,
      textDecoration: "none",
      boxShadow: "0 25px 50px -12px rgba(168,85,247,.35)",
      border: "1px solid rgba(192,132,252,.5)",
      cursor: "pointer"
    }
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "sparkles",
    size: 22
  }), "Get your own AI platform", /*#__PURE__*/React.createElement(Icon, {
    name: "arrow-right",
    size: 18
  })))), /*#__PURE__*/React.createElement("div", {
    style: {
      position: "absolute",
      bottom: 32,
      left: "50%",
      transform: "translateX(-50%)",
      zIndex: 20,
      animation: "hm-bounce 1.5s infinite"
    }
  }, /*#__PURE__*/React.createElement(Button, {
    variant: "outline",
    className: "hm-btn-pill hm-btn-lg",
    onClick: onSeeMore,
    style: {
      background: "rgba(0,0,0,.2)",
      color: "white",
      borderColor: "rgba(255,255,255,.2)",
      backdropFilter: "blur(6px)"
    }
  }, "See more ", /*#__PURE__*/React.createElement(Icon, {
    name: "chevron-down",
    size: 20
  }))));
}

// ───────────────────────────────────────────────────── Cards section (the two big dual cards)
function CardsSection({
  onSeeMore
}) {
  return /*#__PURE__*/React.createElement("section", {
    style: {
      background: "rgb(241 245 249 / .3)",
      borderBottom: "1px solid var(--hm-border)",
      padding: "80px 24px",
      position: "relative"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      maxWidth: 1100,
      margin: "0 auto"
    }
  }, /*#__PURE__*/React.createElement("p", {
    style: {
      fontSize: 22,
      color: "var(--hm-muted-foreground)",
      textAlign: "center",
      maxWidth: 780,
      margin: "0 auto 48px",
      lineHeight: 1.6,
      textWrap: "pretty"
    }
  }, "You have the talent. Your company has the ambition. We connect the dots \u2014 with AI as the common language."), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "1fr 1fr",
      gap: 32
    }
  }, /*#__PURE__*/React.createElement(Glow, {
    from: "#3b82f6",
    to: "#14b8a6"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      background: "white",
      border: "1px solid rgb(37 99 235 / .2)",
      borderRadius: 16,
      padding: 32
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      gap: 16,
      alignItems: "center",
      marginBottom: 20
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      padding: 12,
      borderRadius: 12,
      background: "rgb(59 130 246 / .1)"
    }
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "brain",
    size: 32,
    color: "#3b82f6"
  })), /*#__PURE__*/React.createElement("h2", {
    style: {
      margin: 0,
      fontSize: 24,
      fontWeight: 700
    }
  }, "Do you want to be a Happy Mate?")), /*#__PURE__*/React.createElement("p", {
    style: {
      color: "var(--hm-muted-foreground)",
      lineHeight: 1.6,
      fontSize: 14,
      margin: 0
    }
  }, "Turn your gaming skills into a real IT career. Learn to build with AI, work on live projects, and get hired \u2014 all in 6 months."), /*#__PURE__*/React.createElement("ul", {
    style: {
      listStyle: "none",
      padding: 0,
      margin: "20px 0",
      fontSize: 14,
      fontWeight: 500
    }
  }, /*#__PURE__*/React.createElement("li", {
    style: {
      display: "flex",
      gap: 12,
      padding: "6px 0"
    }
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "rocket",
    size: 18,
    color: "#3b82f6"
  }), " 6-month AI specialization"), /*#__PURE__*/React.createElement("li", {
    style: {
      display: "flex",
      gap: 12,
      padding: "6px 0"
    }
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "users",
    size: 18,
    color: "#3b82f6"
  }), " Bootcamp at a sports resort"), /*#__PURE__*/React.createElement("li", {
    style: {
      display: "flex",
      gap: 12,
      padding: "6px 0"
    }
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "lightbulb",
    size: 18,
    color: "#3b82f6"
  }), " Real business cases")), /*#__PURE__*/React.createElement(Button, {
    variant: "primary",
    size: "lg",
    style: {
      width: "100%",
      background: "#006A8E"
    }
  }, "Read more ", /*#__PURE__*/React.createElement(Icon, {
    name: "arrow-right",
    size: 18
  })))), /*#__PURE__*/React.createElement(Glow, {
    from: "#f97316",
    to: "#ef4444"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      background: "white",
      border: "1px solid rgb(37 99 235 / .2)",
      borderRadius: 16,
      padding: 32
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      gap: 16,
      alignItems: "center",
      marginBottom: 20
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      padding: 12,
      borderRadius: 12,
      background: "rgb(249 115 22 / .1)"
    }
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "lightbulb",
    size: 32,
    color: "#f97316"
  })), /*#__PURE__*/React.createElement("h2", {
    style: {
      margin: 0,
      fontSize: 24,
      fontWeight: 700
    }
  }, "Do you want to be a Digital Chef?")), /*#__PURE__*/React.createElement("p", {
    style: {
      color: "var(--hm-muted-foreground)",
      lineHeight: 1.6,
      fontSize: 14,
      margin: 0
    }
  }, "Get your own AI-trained IT team \u2014 without hiring. With Dele Mate you share skilled developers, use ready-made recipes, and follow a transparent process from order to delivery."), /*#__PURE__*/React.createElement("ul", {
    style: {
      listStyle: "none",
      padding: 0,
      margin: "20px 0",
      fontSize: 14,
      fontWeight: 500
    }
  }, /*#__PURE__*/React.createElement("li", {
    style: {
      display: "flex",
      gap: 12,
      padding: "6px 0"
    }
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "users",
    size: 18,
    color: "#f97316"
  }), " Shared AI developers via Dele Mate"), /*#__PURE__*/React.createElement("li", {
    style: {
      display: "flex",
      gap: 12,
      padding: "6px 0"
    }
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "rocket",
    size: 18,
    color: "#f97316"
  }), " Production-ready Super Prompt recipes"), /*#__PURE__*/React.createElement("li", {
    style: {
      display: "flex",
      gap: 12,
      padding: "6px 0"
    }
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "brain",
    size: 18,
    color: "#f97316"
  }), " Transparent process from idea to live")), /*#__PURE__*/React.createElement(Button, {
    variant: "outline",
    size: "lg",
    style: {
      width: "100%"
    }
  }, "See how it works ", /*#__PURE__*/React.createElement(Icon, {
    name: "arrow-right",
    size: 18
  })))))), /*#__PURE__*/React.createElement("div", {
    style: {
      position: "absolute",
      bottom: 32,
      right: 32,
      animation: "hm-bounce 1.5s infinite"
    }
  }, /*#__PURE__*/React.createElement(Button, {
    variant: "success",
    className: "hm-btn-pill hm-btn-lg",
    onClick: onSeeMore
  }, "See more ", /*#__PURE__*/React.createElement(Icon, {
    name: "chevron-down",
    size: 20
  }))));
}
Object.assign(window, {
  Icon,
  cx,
  Button,
  Glow,
  Logo,
  Wordmark,
  Nav,
  Footer,
  Eyebrow,
  WelcomeHero,
  CardsSection
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/web/components.jsx", error: String((e && e.message) || e) }); }

// ui_kits/web/screens.jsx
try { (() => {
// Happy Mates UI kit — sections and screens
// Composes the smaller components from components.jsx

const HUBS = [{
  title: "Plans & Process",
  description: "Like a fast-food restaurant, but for IT services. We map your needs to small, medium, or large standardized tasks. See how our Delivery Journey works.",
  tag: "Delivery",
  gradient: ["#f97316", "#f59e0b"],
  button: "#f97316",
  bg: "linear-gradient(135deg,#f97316,#fbbf24,#1a0a0a)"
}, {
  title: "The Digital Sourdough",
  description: "See how we use 'The Sourdough' as our fundamental template with best practices, so we never start from scratch when baking new digital products.",
  tag: "Foundation",
  gradient: ["#f59e0b", "#eab308"],
  button: "#f59e0b",
  bg: "linear-gradient(135deg,#fbbf24,#06b6d4,#0f172a)"
}, {
  title: "Operations & Security",
  description: "Our SOC monitors systems 24/7 with Zero Trust architecture and automatic health checks to keep your digital diner safe.",
  tag: "Operations",
  gradient: ["#6366f1", "#a855f7"],
  button: "#6366f1",
  bg: "linear-gradient(135deg,#6366f1,#a855f7,#0f172a)"
}, {
  title: "Your Daily Workflow",
  description: "Clear tasks in a Kanban board, safe code experiments in branches, and friendly AI support that makes the everyday predictable and calm for gamers.",
  tag: "Workflow",
  gradient: ["#14b8a6", "#10b981"],
  button: "#14b8a6",
  bg: "linear-gradient(135deg,#14b8a6,#22d3ee,#0f172a)"
}];
function HubsSection() {
  return /*#__PURE__*/React.createElement("section", {
    style: {
      background: "white",
      borderBottom: "1px solid var(--hm-border)",
      padding: "96px 24px"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      maxWidth: 1200,
      margin: "0 auto"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      textAlign: "center",
      marginBottom: 64
    }
  }, /*#__PURE__*/React.createElement("h2", {
    className: "hm-h2",
    style: {
      margin: 0
    }
  }, "Explore our hubs"), /*#__PURE__*/React.createElement("p", {
    className: "hm-lead",
    style: {
      maxWidth: 640,
      margin: "16px auto 0"
    }
  }, "Dive into the core areas of the Happy Mates methodology and discover how we structure everything from daily work to enterprise security.")), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 96
    }
  }, HUBS.map((hub, i) => /*#__PURE__*/React.createElement("div", {
    key: hub.title,
    style: {
      display: "grid",
      gridTemplateColumns: "1fr 1fr",
      gap: 48,
      alignItems: "center",
      direction: i % 2 === 1 ? "rtl" : "ltr"
    }
  }, /*#__PURE__*/React.createElement(Glow, {
    from: hub.gradient[0],
    to: hub.gradient[1],
    radius: 18,
    style: {
      direction: "ltr"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      borderRadius: 16,
      overflow: "hidden",
      border: "1px solid var(--hm-border)",
      aspectRatio: "16/9",
      background: hub.bg,
      position: "relative",
      boxShadow: "var(--hm-shadow-lg)"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      position: "absolute",
      top: 16,
      left: 16,
      background: `linear-gradient(90deg, ${hub.gradient[0]}, ${hub.gradient[1]})`,
      color: "white",
      padding: "4px 12px",
      borderRadius: 999,
      fontSize: 11,
      fontWeight: 700,
      letterSpacing: ".08em",
      textTransform: "uppercase",
      boxShadow: "var(--hm-shadow-md)"
    }
  }, hub.tag))), /*#__PURE__*/React.createElement("div", {
    style: {
      direction: "ltr",
      display: "flex",
      flexDirection: "column",
      gap: 20
    }
  }, /*#__PURE__*/React.createElement("h3", {
    style: {
      margin: 0,
      fontSize: 36,
      fontWeight: 700,
      letterSpacing: "-0.01em"
    }
  }, hub.title), /*#__PURE__*/React.createElement("p", {
    style: {
      margin: 0,
      fontSize: 17,
      color: "var(--hm-muted-foreground)",
      lineHeight: 1.6
    }
  }, hub.description), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(Button, {
    className: "hm-btn-pill",
    style: {
      background: hub.button,
      color: "white",
      border: "none"
    }
  }, "Learn more ", /*#__PURE__*/React.createElement(Icon, {
    name: "arrow-right",
    size: 16
  })))))))));
}
const NEWS = [{
  id: 1,
  date: "Mar 3, 2026",
  title: "Mate Trainee — introduction night",
  desc: "Join the introduction night at X-Padel Helsingør and meet the team building the next AI-specialised cohort.",
  bg: "linear-gradient(135deg,#118AB2,#06D6A0,#FCBF49)"
}, {
  id: 2,
  date: "Feb 18, 2026",
  title: "Playitas Bootcamp 2026 announced",
  desc: "Ten days at a sports resort. Mornings of code, afternoons of padel. Real business cases on every project.",
  bg: "linear-gradient(135deg,#f97316,#fbbf24,#06d6a0)"
}, {
  id: 3,
  date: "Feb 2, 2026",
  title: "Dele Mate opens enrolment",
  desc: "Our shared-developer programme reaches its first cohort. Pricing, terms and how to apply.",
  bg: "linear-gradient(135deg,#a855f7,#6366f1,#0f172a)"
}];
function NewsSection() {
  return /*#__PURE__*/React.createElement("section", {
    style: {
      background: "rgb(241 245 249 / .5)",
      borderBlock: "1px solid var(--hm-border)",
      padding: "80px 24px"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      maxWidth: 1200,
      margin: "0 auto"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "flex-end",
      justifyContent: "space-between",
      gap: 24,
      marginBottom: 48,
      flexWrap: "wrap"
    }
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(Eyebrow, null, "Latest news"), /*#__PURE__*/React.createElement("h2", {
    className: "hm-h2",
    style: {
      margin: "16px 0 8px"
    }
  }, "News & Events"), /*#__PURE__*/React.createElement("p", {
    style: {
      color: "var(--hm-muted-foreground)",
      fontSize: 17,
      maxWidth: 640,
      margin: 0
    }
  }, "Stay updated on the latest news from the Happy Mates community, upcoming bootcamps, and inspiring stories from our students.")), /*#__PURE__*/React.createElement(Button, {
    variant: "outline"
  }, "View all news ", /*#__PURE__*/React.createElement(Icon, {
    name: "arrow-right",
    size: 16
  }))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "repeat(3,1fr)",
      gap: 32
    }
  }, NEWS.map(a => /*#__PURE__*/React.createElement("article", {
    key: a.id,
    style: {
      background: "white",
      borderRadius: 16,
      border: "1px solid var(--hm-border)",
      overflow: "hidden",
      boxShadow: "var(--hm-shadow-sm)",
      transition: "box-shadow 300ms"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      aspectRatio: "16/9",
      background: a.bg
    }
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      padding: 24
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 6,
      color: "var(--hm-muted-foreground)",
      fontSize: 13,
      marginBottom: 10
    }
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "calendar",
    size: 14
  }), /*#__PURE__*/React.createElement("time", null, a.date)), /*#__PURE__*/React.createElement("h3", {
    style: {
      margin: "0 0 8px",
      fontSize: 20,
      fontWeight: 700,
      lineHeight: 1.25
    }
  }, a.title), /*#__PURE__*/React.createElement("p", {
    style: {
      margin: 0,
      color: "var(--hm-muted-foreground)",
      fontSize: 14,
      lineHeight: 1.55
    }
  }, a.desc), /*#__PURE__*/React.createElement("a", {
    style: {
      display: "inline-flex",
      alignItems: "center",
      gap: 6,
      marginTop: 18,
      color: "var(--hm-primary)",
      fontWeight: 500,
      fontSize: 14,
      cursor: "pointer"
    }
  }, "Read article ", /*#__PURE__*/React.createElement(Icon, {
    name: "arrow-right",
    size: 14
  }))))))));
}

// ───────────────────────────────────────────────────── Companies screen
function CompaniesScreen() {
  const features = [{
    title: "Dele Mate",
    subtitle: "Shared AI developers",
    icon: "users",
    color: "#f97316",
    iconBg: "rgb(249 115 22 / .1)",
    desc: "Share an AI-trained IT team with other businesses. Get dedicated development hours without the overhead of hiring — our Mates work on your tasks as if they were your own team.",
    bg: "linear-gradient(135deg,#fbbf24,#f97316,#7c2d12)"
  }, {
    title: "Super Prompt Recipes",
    subtitle: "Plug-and-play modules",
    icon: "sparkles",
    color: "var(--hm-primary)",
    iconBg: "rgb(37 99 235 / .1)",
    desc: "Browse our catalog of production-ready recipes — from authentication to AI chat widgets. Buy individually or get unlimited access with a Business Membership.",
    bg: "linear-gradient(135deg,#06b6d4,#3b82f6,#1e293b)"
  }, {
    title: "Our Process",
    subtitle: "From idea to live",
    icon: "clipboard-list",
    color: "#14b8a6",
    iconBg: "rgb(20 184 166 / .1)",
    desc: "See how we work — from standardized development tasks, through secure deployment, to 24/7 monitoring. Transparent, predictable, and fully documented.",
    bg: "linear-gradient(135deg,#fcbf49,#e4002b,#1a0a0a)"
  }];
  return /*#__PURE__*/React.createElement("main", null, /*#__PURE__*/React.createElement("section", {
    style: {
      padding: "96px 24px 32px"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      maxWidth: 1200,
      margin: "0 auto"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      gap: 16,
      alignItems: "center",
      marginBottom: 24
    }
  }, /*#__PURE__*/React.createElement("a", {
    style: {
      color: "var(--hm-muted-foreground)",
      textDecoration: "none",
      fontSize: 14,
      cursor: "pointer",
      display: "inline-flex",
      gap: 6
    }
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "arrow-left",
    size: 14
  }), "Back to front page"), /*#__PURE__*/React.createElement(Eyebrow, {
    color: "orange"
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "lightbulb",
    size: 14
  }), "For Companies")), /*#__PURE__*/React.createElement("h1", {
    className: "hm-h1",
    style: {
      fontSize: "clamp(40px, 6vw, 80px)",
      margin: 0
    }
  }, "Your own AI team \u2014 ", /*#__PURE__*/React.createElement("span", {
    style: {
      background: "var(--hm-hub-companies)",
      WebkitBackgroundClip: "text",
      backgroundClip: "text",
      color: "transparent"
    }
  }, "without hiring")), /*#__PURE__*/React.createElement("p", {
    style: {
      fontSize: 19,
      color: "var(--hm-muted-foreground)",
      lineHeight: 1.6,
      marginTop: 24,
      maxWidth: 720
    }
  }, "With Dele Mate you share skilled, AI-trained developers with other businesses. Combine that with ready-made recipes and a transparent process \u2014 and you have a complete IT capability at a fraction of the cost."))), /*#__PURE__*/React.createElement("section", {
    style: {
      padding: "32px 24px 96px"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      maxWidth: 1200,
      margin: "0 auto",
      display: "flex",
      flexDirection: "column",
      gap: 32
    }
  }, features.map((f, i) => /*#__PURE__*/React.createElement("div", {
    key: f.title,
    style: {
      background: "white",
      border: "1px solid var(--hm-border)",
      borderRadius: 16,
      padding: 32,
      display: "grid",
      gridTemplateColumns: i === 1 ? "1fr 1fr" : "1fr",
      gap: 32,
      alignItems: "stretch",
      boxShadow: "var(--hm-shadow-sm)"
    }
  }, i === 1 && /*#__PURE__*/React.createElement("div", {
    style: {
      order: 2,
      borderRadius: 16,
      background: f.bg,
      aspectRatio: "4/3"
    }
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 16,
      order: 1
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 14
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      padding: 10,
      borderRadius: 12,
      background: f.iconBg
    }
  }, /*#__PURE__*/React.createElement(Icon, {
    name: f.icon,
    size: 22,
    color: f.color
  })), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("h3", {
    style: {
      margin: 0,
      fontSize: 22,
      fontWeight: 700
    }
  }, f.title), /*#__PURE__*/React.createElement("div", {
    style: {
      color: f.color,
      fontWeight: 600,
      fontSize: 14
    }
  }, f.subtitle))), /*#__PURE__*/React.createElement("p", {
    style: {
      margin: 0,
      color: "var(--hm-muted-foreground)",
      fontSize: 15,
      lineHeight: 1.6
    }
  }, f.desc), /*#__PURE__*/React.createElement("a", {
    style: {
      color: f.color,
      fontWeight: 500,
      fontSize: 14,
      display: "inline-flex",
      gap: 6,
      alignItems: "center",
      cursor: "pointer"
    }
  }, "Learn more ", /*#__PURE__*/React.createElement(Icon, {
    name: "arrow-right",
    size: 14
  }))))))), /*#__PURE__*/React.createElement("section", {
    style: {
      padding: "32px 24px 96px",
      textAlign: "center"
    }
  }, /*#__PURE__*/React.createElement("h2", {
    className: "hm-h2",
    style: {
      margin: "0 0 16px"
    }
  }, "Ready to get started?"), /*#__PURE__*/React.createElement("p", {
    style: {
      color: "var(--hm-muted-foreground)",
      fontSize: 17,
      maxWidth: 640,
      margin: "0 auto 24px"
    }
  }, "Contact us for a free conversation about how Dele Mate, our recipes, and our processes can help your business."), /*#__PURE__*/React.createElement(Button, {
    variant: "primary",
    size: "lg"
  }, "Contact us ", /*#__PURE__*/React.createElement(Icon, {
    name: "arrow-right",
    size: 16
  }))));
}

// ───────────────────────────────────────────────────── Recipes (super prompts) screen
const RECIPES = [{
  name: "Auth Gateway",
  tag: "AUTH",
  color: "#7c3aed",
  icon: "shield",
  tagline: "Drop-in authentication for any stack",
  price: 499,
  complexity: "📝 Medium"
}, {
  name: "Data Pipeline",
  tag: "DATA",
  color: "#0d9488",
  icon: "database",
  tagline: "ETL that works with any database",
  price: 499,
  complexity: "🔄 Advanced"
}, {
  name: "AI Chat Widget",
  tag: "AI",
  color: "#6366f1",
  icon: "message-square",
  tagline: "Embeddable chat with streaming LLM responses",
  price: 499,
  complexity: "🍞 Simple"
}, {
  name: "Webhook Orchestrator",
  tag: "INTEGRATION",
  color: "#16a34a",
  icon: "webhook",
  tagline: "Receive, validate, route, and retry webhooks",
  price: 499,
  complexity: "📝 Medium"
}, {
  name: "Dashboard Builder",
  tag: "UI",
  color: "#e11d48",
  icon: "layout-dashboard",
  tagline: "Data-driven dashboard with drag-and-drop widgets",
  price: 499,
  complexity: "📝 Medium"
}, {
  name: "PDF Report Engine",
  tag: "DATA",
  color: "#0d9488",
  icon: "file-text",
  tagline: "Generate branded PDFs from structured data",
  price: 499,
  complexity: "🍞 Simple"
}, {
  name: "Multi-tenant Isolation",
  tag: "AUTH",
  color: "#7c3aed",
  icon: "users",
  tagline: "Tenant-scoped data, roles, and billing for SaaS",
  price: 499,
  complexity: "🔄 Advanced"
}, {
  name: "Event Sourcing Core",
  tag: "DATA",
  color: "#0d9488",
  icon: "git-branch",
  tagline: "Full event-sourced architecture with projections",
  price: 499,
  complexity: "🔄 Advanced"
}, {
  name: "Email & Notification Hub",
  tag: "INTEGRATION",
  color: "#16a34a",
  icon: "mail",
  tagline: "Transactional email + push + SMS via unified API",
  price: 499,
  complexity: "🍞 Simple"
}];
const RECIPE_BG = "linear-gradient(135deg,#fbbf24 0%,#06b6d4 50%,#0f172a 100%)";
function ProductCard({
  r
}) {
  const tagBg = `${r.color}26`; // ~15% alpha
  return /*#__PURE__*/React.createElement("article", {
    style: {
      background: "white",
      borderRadius: 16,
      border: "1px solid var(--hm-border)",
      overflow: "hidden",
      boxShadow: "var(--hm-shadow-sm)",
      display: "flex",
      flexDirection: "column",
      cursor: "pointer"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      aspectRatio: "16/10",
      background: RECIPE_BG,
      position: "relative"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      position: "absolute",
      inset: 0,
      background: "radial-gradient(circle at 30% 60%, rgba(255,255,255,.2), transparent 50%)"
    }
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      padding: 22,
      display: "flex",
      flexDirection: "column",
      flex: 1
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      justifyContent: "space-between",
      alignItems: "flex-start",
      marginBottom: 14
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      padding: 10,
      borderRadius: 12,
      background: "var(--hm-muted)",
      color: "var(--hm-muted-foreground)"
    }
  }, /*#__PURE__*/React.createElement(Icon, {
    name: r.icon,
    size: 20
  })), /*#__PURE__*/React.createElement("span", {
    style: {
      padding: "3px 9px",
      borderRadius: 999,
      fontSize: 10,
      fontWeight: 700,
      textTransform: "uppercase",
      letterSpacing: ".08em",
      background: tagBg,
      color: r.color
    }
  }, r.tag)), /*#__PURE__*/React.createElement("h3", {
    style: {
      margin: "0 0 4px",
      fontSize: 17,
      fontWeight: 700
    }
  }, r.name), /*#__PURE__*/React.createElement("p", {
    style: {
      margin: "0 0 14px",
      color: "var(--hm-muted-foreground)",
      fontSize: 13,
      lineHeight: 1.5,
      flex: 1
    }
  }, r.tagline), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      justifyContent: "space-between",
      alignItems: "center",
      paddingTop: 12,
      borderTop: "1px solid var(--hm-border)"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      gap: 12,
      alignItems: "center"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 12,
      color: "var(--hm-muted-foreground)"
    }
  }, r.complexity), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 14,
      fontWeight: 700
    }
  }, r.price, " DKK")), /*#__PURE__*/React.createElement(Icon, {
    name: "arrow-right",
    size: 16,
    color: "var(--hm-muted-foreground)"
  }))));
}
function RecipesScreen() {
  const [filter, setFilter] = useState("All");
  const tabs = ["All", "Auth", "Data", "AI", "Integration", "UI"];
  const filtered = filter === "All" ? RECIPES : RECIPES.filter(r => r.tag.toLowerCase() === filter.toLowerCase());
  return /*#__PURE__*/React.createElement("main", {
    style: {
      padding: "64px 24px 96px"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      maxWidth: 1200,
      margin: "0 auto"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      textAlign: "center",
      marginBottom: 48
    }
  }, /*#__PURE__*/React.createElement(Eyebrow, null, /*#__PURE__*/React.createElement(Icon, {
    name: "sparkles",
    size: 14
  }), "Recipe Shop"), /*#__PURE__*/React.createElement("h1", {
    className: "hm-h1",
    style: {
      fontSize: "clamp(40px,5vw,72px)",
      margin: "16px 0 16px"
    }
  }, "Super Prompts"), /*#__PURE__*/React.createElement("p", {
    style: {
      fontSize: 17,
      color: "var(--hm-muted-foreground)",
      maxWidth: 640,
      margin: "0 auto",
      lineHeight: 1.6
    }
  }, "Plug-in recipes for your digital sourdough. Each Super Prompt is a complete, production-ready module recipe that can extend ", /*#__PURE__*/React.createElement("b", null, "Surdej"), " \u2014 or anything else you're building."), /*#__PURE__*/React.createElement("p", {
    style: {
      fontSize: 15,
      color: "var(--hm-muted-foreground)",
      maxWidth: 540,
      margin: "16px auto 0"
    }
  }, "Buy individually for ", /*#__PURE__*/React.createElement("b", {
    style: {
      color: "var(--hm-foreground)"
    }
  }, "499 DKK"), " each, or get ", /*#__PURE__*/React.createElement("b", {
    style: {
      color: "var(--hm-foreground)"
    }
  }, "unlimited access"), " with a Business Membership.")), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      justifyContent: "space-between",
      alignItems: "center",
      marginBottom: 28,
      flexWrap: "wrap",
      gap: 12
    }
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("h2", {
    style: {
      margin: 0,
      fontSize: 28,
      fontWeight: 700
    }
  }, "Explore Super Prompts"), /*#__PURE__*/React.createElement("p", {
    style: {
      margin: "4px 0 0",
      color: "var(--hm-muted-foreground)",
      fontSize: 13
    }
  }, filtered.length, " recipes available")), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      gap: 6,
      flexWrap: "wrap"
    }
  }, tabs.map(t => /*#__PURE__*/React.createElement("button", {
    key: t,
    onClick: () => setFilter(t),
    className: "hm-btn hm-btn-sm hm-btn-pill",
    style: {
      background: filter === t ? "var(--hm-foreground)" : "transparent",
      color: filter === t ? "white" : "var(--hm-foreground)",
      borderColor: filter === t ? "var(--hm-foreground)" : "var(--hm-border)",
      borderWidth: 1,
      borderStyle: "solid"
    }
  }, t)))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "repeat(3,1fr)",
      gap: 24
    }
  }, filtered.map(r => /*#__PURE__*/React.createElement(ProductCard, {
    key: r.name,
    r: r
  }))), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 64,
      padding: 48,
      borderRadius: 24,
      textAlign: "center",
      background: "linear-gradient(135deg, rgb(241 245 249 / .8), rgb(219 234 254 / .6))",
      border: "1px solid var(--hm-border)"
    }
  }, /*#__PURE__*/React.createElement(Eyebrow, null, /*#__PURE__*/React.createElement(Icon, {
    name: "clock",
    size: 14
  }), "1 supported prompt / month"), /*#__PURE__*/React.createElement("h3", {
    style: {
      margin: "16px 0 16px",
      fontSize: 32,
      fontWeight: 700
    }
  }, "Business Membership"), /*#__PURE__*/React.createElement("p", {
    style: {
      color: "var(--hm-muted-foreground)",
      fontSize: 15,
      maxWidth: 520,
      margin: "0 auto 24px",
      lineHeight: 1.6
    }
  }, "Get ", /*#__PURE__*/React.createElement("b", {
    style: {
      color: "var(--hm-foreground)"
    }
  }, "unlimited access"), " to every Super Prompt in the catalog.", /*#__PURE__*/React.createElement("br", null), "Plus ", /*#__PURE__*/React.createElement("b", {
    style: {
      color: "var(--hm-foreground)"
    }
  }, "hands-on implementation support"), " for 1 prompt per month."), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 40,
      fontWeight: 700,
      color: "var(--hm-primary)"
    }
  }, "750 DKK", /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 18,
      color: "var(--hm-muted-foreground)"
    }
  }, "/mo")), /*#__PURE__*/React.createElement(Button, {
    variant: "primary",
    size: "lg",
    className: "hm-btn-pill",
    style: {
      marginTop: 24
    }
  }, "Become a Business Member ", /*#__PURE__*/React.createElement(Icon, {
    name: "arrow-right",
    size: 16
  })))));
}

// ───────────────────────────────────────────────────── App shell with routing
function App() {
  const [screen, setScreen] = useState("home");
  const homeRef = useRef(null);
  const cardsRef = useRef(null);
  const hubsRef = useRef(null);
  const newsRef = useRef(null);
  const scrollTo = ref => ref.current?.scrollTo({
    top: 0,
    behavior: "smooth"
  });
  useEffect(() => {
    // Re-render lucide icons whenever the DOM changes
    window.lucide && window.lucide.createIcons();
  });
  return /*#__PURE__*/React.createElement("div", {
    style: {
      minHeight: "100vh",
      display: "flex",
      flexDirection: "column"
    }
  }, /*#__PURE__*/React.createElement(Nav, {
    active: screen,
    onNavigate: setScreen
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1
    }
  }, screen === "home" && /*#__PURE__*/React.createElement("main", null, /*#__PURE__*/React.createElement("div", {
    ref: homeRef
  }, /*#__PURE__*/React.createElement(WelcomeHero, {
    onSeeMore: () => cardsRef.current?.scrollIntoView({
      behavior: "smooth"
    })
  })), /*#__PURE__*/React.createElement("div", {
    ref: cardsRef
  }, /*#__PURE__*/React.createElement(CardsSection, {
    onSeeMore: () => hubsRef.current?.scrollIntoView({
      behavior: "smooth"
    })
  })), /*#__PURE__*/React.createElement("div", {
    ref: hubsRef
  }, /*#__PURE__*/React.createElement(HubsSection, null)), /*#__PURE__*/React.createElement("div", {
    ref: newsRef
  }, /*#__PURE__*/React.createElement(NewsSection, null))), screen === "companies" && /*#__PURE__*/React.createElement(CompaniesScreen, null), screen === "recipes" && /*#__PURE__*/React.createElement(RecipesScreen, null), (screen === "unge" || screen === "hearts" || screen === "about") && /*#__PURE__*/React.createElement("main", {
    style: {
      padding: "120px 24px",
      textAlign: "center"
    }
  }, /*#__PURE__*/React.createElement(Eyebrow, null, "Placeholder"), /*#__PURE__*/React.createElement("h1", {
    className: "hm-h1",
    style: {
      fontSize: 60,
      margin: "16px 0"
    }
  }, "This screen is in the kit but not yet built."), /*#__PURE__*/React.createElement("p", {
    style: {
      color: "var(--hm-muted-foreground)",
      fontSize: 17
    }
  }, "Use Home / Companies / Recipes from the kit switcher."))), /*#__PURE__*/React.createElement(Footer, {
    onNavigate: setScreen
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      position: "fixed",
      bottom: 16,
      left: "50%",
      transform: "translateX(-50%)",
      background: "rgba(2,8,23,.92)",
      color: "white",
      borderRadius: 999,
      padding: 6,
      display: "flex",
      gap: 4,
      zIndex: 100,
      boxShadow: "var(--hm-shadow-2xl)"
    }
  }, [["home", "Home"], ["companies", "Companies"], ["recipes", "Recipes"]].map(([k, label]) => /*#__PURE__*/React.createElement("button", {
    key: k,
    onClick: () => setScreen(k),
    style: {
      background: screen === k ? "white" : "transparent",
      color: screen === k ? "var(--hm-foreground)" : "white",
      border: "none",
      padding: "8px 16px",
      borderRadius: 999,
      fontWeight: 600,
      fontSize: 13,
      cursor: "pointer",
      fontFamily: "inherit"
    }
  }, label))));
}
Object.assign(window, {
  HubsSection,
  NewsSection,
  CompaniesScreen,
  RecipesScreen,
  ProductCard,
  App
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/web/screens.jsx", error: String((e && e.message) || e) }); }

})();
