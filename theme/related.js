/* The "related documentation" links in each site's footer: <p class="bb-related" data-self="ocean-docs">.
   One list for all sites -- add a site here, then update the submodule in each docs repository. */
(function () {
  var SITES = [
    ["nesting-docs", "pyGETM nesting", "https://bolding.github.io/nesting-docs/"],
    ["config-docs", "pygetm-config", "https://bolding.github.io/config-docs/"],
    ["ocean-docs", "ocean-* packages", "https://bolding.github.io/ocean-docs/"],
    ["oceanicu_3d", "OceanICU shelf-sea models", "https://bolding-bruggeman.com/oceanicu_3d/"]
  ];
  document.querySelectorAll(".bb-related").forEach(function (el) {
    var self = el.getAttribute("data-self");
    var links = SITES.filter(function (s) { return s[0] !== self; })
      .map(function (s) { return '<a href="' + s[2] + '">' + s[1] + "</a>"; });
    el.innerHTML = "Related documentation: " + links.join(" · ");
  });
})();
