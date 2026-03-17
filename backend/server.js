import express from "express"
import cors from "cors"
import dotenv from "dotenv"
import path from "path"

dotenv.config({ path: path.resolve("../.env") })

import analyzeRoute from "./routes/analyze.js"

const app = express()

app.use(cors())
app.use(express.json())

app.use("/api/analyze", analyzeRoute)

app.listen(process.env.PORT || 5000, () => {
    console.log(`🚀 Server running on port ${process.env.PORT}`)
})