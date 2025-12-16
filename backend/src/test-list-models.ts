import fetch from "node-fetch";

const API_KEY = "AIzaSyDohkX7ZLt0OLtlYtwfcSPOS9dFqj_rjUI";

async function listModels() {
    const res = await fetch(
        "https://generativelanguage.googleapis.com/v1/models?key=" + API_KEY
    );

    const data = await res.json();
    console.log(JSON.stringify(data, null, 2));
}

listModels();
