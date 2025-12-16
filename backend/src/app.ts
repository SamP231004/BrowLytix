import express from "express"
import analyzeRouter from "./routes/analyze.route.js"

const app = express()

app.use(express.json())

app.use("/api/analyze", analyzeRouter)

export default app