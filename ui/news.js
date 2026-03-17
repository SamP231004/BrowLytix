let debounceTimer = null;

function getUserId() {
    let id = localStorage.getItem("browlytix_user");

    if (!id) {
        id = "user_" + Math.random().toString(36).substring(2, 10);
        localStorage.setItem("browlytix_user", id);
    }

    return id;
}

export function updateNews(contextText) {
    // 🔥 debounce (300ms)
    clearTimeout(debounceTimer);

    debounceTimer = setTimeout(() => {
        fetchNews(contextText);
    }, 300);
}

async function fetchNews(contextText) {
    try {
        renderLoading();

        const res = await fetch("http://localhost:5000/api/analyze", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                userId: getUserId(),
                title: contextText,
                url: window.location.href
            })
        });

        if (!res.ok) throw new Error("API error");

        const data = await res.json();

        renderNews(data.links);

    } catch (err) {
        console.error("News fetch error:", err);
        renderError();
    }
}

function renderNews(articles) {
    const list = document.querySelector(".news-list");
    list.innerHTML = "";

    if (!articles || articles.length === 0) {
        list.innerHTML = "<li>No relevant news</li>";
        return;
    }

    articles.forEach(article => {
        const li = document.createElement("li");

        li.innerHTML = `
      <a href="${article.url}" target="_blank" rel="noopener noreferrer">
        ${article.title}
      </a>
    `;

        list.appendChild(li);
    });
}

function renderLoading() {
    const list = document.querySelector(".news-list");
    list.innerHTML = "<li>Loading news...</li>";
}

function renderError() {
    const list = document.querySelector(".news-list");
    list.innerHTML = "<li>Failed to load news</li>";
}